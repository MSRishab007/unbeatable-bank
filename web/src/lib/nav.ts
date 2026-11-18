/**
 * Where the app is and how to move, for code outside React components.
 *
 * The real app uses clean paths (/home, /pay). The offline demo keeps hash paths (/#/home),
 * so its build still works from any folder or static host without a server.
 */
export const HASH_ROUTES = import.meta.env.MODE === "demo";

/** The app's current path, e.g. "/pay". */
export const here = (): string => (HASH_ROUTES ? location.hash.slice(1).split("?")[0] || "/" : location.pathname);

/** Go to a path in the app without reloading the page. */
export function go(path: string) {
  if (HASH_ROUTES) { location.hash = path; return; }
  history.pushState(null, "", path);
  dispatchEvent(new PopStateEvent("popstate"));   // the router listens for this
}

/** A full link to a page of the app, e.g. for a QR code. */
export const appUrl = (path: string): string => `${location.origin}${HASH_ROUTES ? "/#" : ""}${path}`;

/**
 * Old links and printed QR codes used /#/pay?to=...; keep them working by moving the hash
 * path into the real path before the router starts.
 */
export function upgradeHashLink() {
  if (!HASH_ROUTES && location.hash.startsWith("#/")) history.replaceState(null, "", location.hash.slice(1));
}
