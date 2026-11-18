/** Rupees in words, the Indian way: 1,25,000.50 -> "Rupees One Lakh Twenty Five Thousand and Fifty Paise Only". */
const ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve",
  "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"];
const TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"];

function upTo99(n: number): string {
  return n < 20 ? ONES[n]! : `${TENS[Math.floor(n / 10)]}${n % 10 ? " " + ONES[n % 10] : ""}`;
}

function whole(n: number): string {
  const parts: string[] = [];
  const crore = Math.floor(n / 1e7), lakh = Math.floor((n % 1e7) / 1e5), thousand = Math.floor((n % 1e5) / 1e3);
  const hundred = Math.floor((n % 1e3) / 100), rest = n % 100;
  if (crore) parts.push(`${whole(crore)} Crore`);
  if (lakh) parts.push(`${upTo99(lakh)} Lakh`);
  if (thousand) parts.push(`${upTo99(thousand)} Thousand`);
  if (hundred) parts.push(`${ONES[hundred]} Hundred`);
  if (rest) parts.push(upTo99(rest));
  return parts.join(" ");
}

export function rupeesInWords(value: string): string {
  const [r = "0", p = ""] = value.split(".");
  const rupees = Number(r || 0), paise = Number((p + "00").slice(0, 2));
  if (!rupees && !paise) return "";
  const head = rupees ? `${rupees === 1 ? "Rupee" : "Rupees"} ${whole(rupees)}` : "";
  const tail = paise ? `${head ? " and " : ""}${upTo99(paise)} Paise` : "";
  return `${head}${tail} Only`;
}
