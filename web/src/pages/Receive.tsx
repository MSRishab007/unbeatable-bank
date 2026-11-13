import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import QRCode from "qrcode";
import { Copy, Download } from "lucide-react";
import { toast } from "sonner";
import { Empty, PageHead } from "@/components/AppShell";
import { Card, Skeleton } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/input";
import { useAccounts } from "@/lib/query";
import { payable, PRODUCT } from "@/lib/types";
import { copyText, groupDigits, inr, toPaise } from "@/lib/utils";

export default function Receive() {
  const [params] = useSearchParams();
  const accounts = useAccounts();
  const list = useMemo(() => (accounts.data || []).filter((a) => payable(a) && a.status === "ACTIVE"), [accounts.data]);
  const [account, setAccount] = useState("");
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    if (!account && list[0]) setAccount(list.find((a) => a.account_id === params.get("account"))?.account_id || list[0].account_id);
  }, [list, account, params]);
  const a = list.find((x) => x.account_id === account);
  const paise = toPaise(amount);
  const link = useMemo(() => {
    if (!a) return "";
    const q = new URLSearchParams({ to: a.account_number });
    if (paise) q.set("amount", (paise / 100).toFixed(2));
    if (note.trim()) q.set("note", note.trim());
    return `${location.origin}/#/pay?${q}`;
  }, [a, paise, note]);
  useEffect(() => {
    if (link && canvas.current) QRCode.toCanvas(canvas.current, link, { width: 260, margin: 2, color: { dark: "#12107a", light: "#ffffff" } });
  }, [link]);

  if (accounts.isLoading) return <Skeleton className="h-96" />;
  if (!list.length) return <><PageHead title="Receive" /><Empty title="You need an active account first" text="Open an account and get your KYC verified." action={<Button asChild><Link to="/home">Go to Home</Link></Button>} /></>;
  return (
    <div>
      <PageHead title="Receive" lead="Show this code to anyone with the app, or send them the link." />
      <div className="grid items-start gap-6 lg:grid-cols-[1fr_380px]">
        <Card className="order-2 space-y-5 p-5 sm:p-6 lg:order-1">
          <Field label="Into" htmlFor="rc-acct">
            <Select id="rc-acct" value={account} onChange={(e) => setAccount(e.target.value)}>
              {list.map((x) => <option key={x.account_id} value={x.account_id}>{PRODUCT[x.product_type]} {groupDigits(x.account_number)}</option>)}
            </Select>
          </Field>
          <Field label={<>Amount <span className="font-normal text-muted">(optional)</span></>} htmlFor="rc-amt">
            <Input id="rc-amt" inputMode="decimal" placeholder="₹ Payer chooses" value={amount} onChange={(e) => setAmount(e.target.value)} />
          </Field>
          <Field label={<>Note <span className="font-normal text-muted">(optional)</span></>} htmlFor="rc-note">
            <Input id="rc-note" maxLength={60} placeholder="What it's for" value={note} onChange={(e) => setNote(e.target.value)} />
          </Field>
        </Card>
        <Card className="order-1 flex flex-col items-center rounded-[30px] bg-cobalt p-6 text-white lg:order-2">
          <div className="rounded-3xl bg-white p-3"><canvas id="qr" ref={canvas} role="img" aria-label="Payment QR code" className="block size-[220px]" /></div>
          <p className="mt-5 text-center font-semibold">{paise ? `Asks for ${inr(paise)}` : "Payer chooses the amount"}</p>
          <p className="text-center text-sm text-white/70 num">into {a ? groupDigits(a.account_number) : ""}</p>
          <input readOnly id="pay-link" value={link} className="sr-only" aria-label="Payment link" />
          <div className="mt-5 flex gap-2">
            <Button variant="light" size="sm" onClick={async () => { if (await copyText(link)) toast.success("Payment link copied"); }}><Copy /> Copy link</Button>
            <Button variant="ghost" size="sm" className="text-white hover:bg-white/10" onClick={() => {
              const el = document.createElement("a"); el.href = canvas.current!.toDataURL("image/png"); el.download = "unbeatable-bank-qr.png"; el.click();
            }}><Download /> Save</Button>
          </div>
        </Card>
      </div>
    </div>
  );
}
