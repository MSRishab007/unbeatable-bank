# Load test and fraud simulator

## Load test (k6): autoscaling under sustained traffic

```bash
make loadtest                      # against the GKE deployment
k6 run loadtest/payments.js        # against a local stack on :8080
```

About 10 minutes of traffic: real payments plus dashboard reads, ramping up
for 2 minutes, steady for 6, then down. In a second terminal:

```bash
kubectl -n bank get hpa -w
```

You should see `gateway`, `intake` and `risk` go from 1 replica to several and,
a couple of minutes after the test ends, back to 1. The same is visible in
Grafana (`make grafana`, panel "HPA replicas"). k6 fails the run if more than
2% of requests error or the 95th percentile latency exceeds 1.5 s.

Raise the load with `-e CUSTOMERS=400 -e BROWSE_RPS=300`.

## Fraud simulator: one persona per fraud type

```bash
make simulate                                  # GKE
python loadtest/simulator.py                   # local
python loadtest/simulator.py --with-flink      # includes the mule ring (needs the Flink job: make flink-up)
```

| Persona | What happens | Caught by |
|---|---|---|
| `normal` | Everyday payments | Nobody: honest customers see no friction |
| `bot` | 30 rapid payments | Rate Limit Coordinator, velocity rule |
| `takeover` | Login from a new device in another country, then a large payment | Risk Detection: new device, impossible travel, new payee |
| `probing` | Tiny test payments, then a big one | Risk Detection: micro-probing |
| `structuring` | Repeated amounts just under Rs 50,000 | Risk Detection: structuring |
| `fd-break` | Fixed deposit closed early, money sent to a stranger | Risk Detection, signalled by Deposits through DynaStore |
| `mule-ring` | Six victims pay one account | Flink windowed aggregation, then Fraud Alert |

Both tools need `ALLOW_SIM_HEADERS=true` on the gateway (set in development and
in the demo deployment), which lets a simulated customer claim its own IP
address and location.
