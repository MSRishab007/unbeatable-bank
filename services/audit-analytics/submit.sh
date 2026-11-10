#!/bin/bash
# Starts the Audit Analytics Flink SQL job on a Dataproc cluster.
#
# Terraform uploads this file and the sql/ folder to Cloud Storage and sets it
# as the cluster's initialization action (infra/main/dataproc.tf), so creating
# the cluster starts the job. It can also be run by hand on the master node:
#
#     sudo bash /opt/audit-analytics/submit.sh run
#
# Log: /var/log/audit-analytics.log (also in Cloud Logging).

set -euo pipefail

JOB_DIR=/opt/audit-analytics
LOG=/var/log/audit-analytics.log
FLINK_HOME=/usr/lib/flink

metadata() {
  curl -sf -H "Metadata-Flavor: Google" \
    "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1" || true
}

# ------------------------------------------------------------------ phase 1: initialization action
# Runs while the cluster is still being created, so it only fetches the files
# and schedules phase 2 for when YARN is up.
if [[ "${1:-init}" == "init" ]]; then
  ROLE=$(metadata dataproc-role)
  [[ "$ROLE" == "Master" ]] || exit 0
  BUCKET=$(metadata job-bucket)
  mkdir -p "$JOB_DIR"
  gsutil -m cp -r "gs://${BUCKET}/audit-analytics/*" "$JOB_DIR/"
  chmod +x "$JOB_DIR/submit.sh"
  systemd-run --unit=audit-analytics-submit --description="Submit the Audit Analytics Flink job" \
    /bin/bash "$JOB_DIR/submit.sh" run
  exit 0
fi

# ------------------------------------------------------------------ phase 2: submit the job
exec >>"$LOG" 2>&1
echo "=== $(date -Is) submitting audit-analytics"

BUCKET=$(metadata job-bucket)
NODEPORT=$(metadata kafka-nodeport)
NODE_TAG=$(metadata gke-node-tag)
export HADOOP_CLASSPATH=$(hadoop classpath)

echo "waiting for YARN ..."
for _ in $(seq 1 60); do
  yarn node -list >/dev/null 2>&1 && break
  sleep 5
done

# Kafka runs on GKE and is exposed on every node's internal IP at a fixed NodePort.
ZONE=$(curl -sf -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/instance/zone | awk -F/ '{print $NF}')
BOOTSTRAP=""
for _ in $(seq 1 30); do
  BOOTSTRAP=$(gcloud compute instances list --zones "$ZONE" \
    --filter="tags.items=${NODE_TAG} AND status=RUNNING" \
    --format="value(networkInterfaces[0].networkIP)" | sed "s/$/:${NODEPORT}/" | paste -sd, -)
  [[ -n "$BOOTSTRAP" ]] && break
  echo "no running GKE nodes yet; retrying"
  sleep 10
done
[[ -n "$BOOTSTRAP" ]] || { echo "ERROR: could not find GKE nodes for Kafka"; exit 1; }
echo "kafka bootstrap: $BOOTSTRAP"

# The Kafka SQL connector is not part of the Flink distribution. Its version
# has to match the Flink version on the image.
FLINK_VERSION=$("$FLINK_HOME/bin/flink" --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
MAVEN=https://repo1.maven.org/maven2/org/apache/flink/flink-sql-connector-kafka
case "$FLINK_VERSION" in
  1.15.*) JAR_URL="$MAVEN/1.15.4/flink-sql-connector-kafka-1.15.4.jar" ;;
  1.16.*) JAR_URL="$MAVEN/1.16.3/flink-sql-connector-kafka-1.16.3.jar" ;;
  1.17.*) JAR_URL="$MAVEN/1.17.2/flink-sql-connector-kafka-1.17.2.jar" ;;
  1.18.*) JAR_URL="$MAVEN/3.2.0-1.18/flink-sql-connector-kafka-3.2.0-1.18.jar" ;;
  1.19.*) JAR_URL="$MAVEN/3.3.0-1.19/flink-sql-connector-kafka-3.3.0-1.19.jar" ;;
  1.20.*) JAR_URL="$MAVEN/3.3.0-1.20/flink-sql-connector-kafka-3.3.0-1.20.jar" ;;
  *) echo "ERROR: no Kafka connector known for Flink $FLINK_VERSION; add it to submit.sh"; exit 1 ;;
esac
JAR="$JOB_DIR/$(basename "$JAR_URL")"
[[ -f "$JAR" ]] || curl -sfL -o "$JAR" "$JAR_URL"
echo "flink $FLINK_VERSION, connector $(basename "$JAR")"

# One SQL script: tables, views, then the statement set that starts the job.
SQL="$JOB_DIR/job.sql"
cat "$JOB_DIR"/sql/00_kafka_tables.sql "$JOB_DIR"/sql/10_views.sql "$JOB_DIR"/sql/20_inserts.sql \
  | sed -e "s|\${KAFKA_BOOTSTRAP}|${BOOTSTRAP}|g" \
        -e "s|\${CHECKPOINT_DIR}|gs://${BUCKET}/checkpoints/audit-analytics|g" >"$SQL"

# A long-running Flink session on YARN, then the SQL client submits the job to it.
if ! yarn application -list 2>/dev/null | grep -q audit-analytics; then
  "$FLINK_HOME/bin/yarn-session.sh" -d -nm audit-analytics -jm 1024m -tm 2048m -s 2
  sleep 15
fi
"$FLINK_HOME/bin/sql-client.sh" -j "$JAR" -f "$SQL"
echo "=== $(date -Is) submitted. Check with: yarn application -list ; flink list"
