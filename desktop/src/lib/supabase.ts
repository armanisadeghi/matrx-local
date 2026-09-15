/**
 * The one Supabase client for the webview (FS-C5b, SPEC-CUSTODY §6, D17).
 *
 * It is constructed with the `accessToken` option, which has two consequences, both intended:
 *
 * 1. **This process holds no session.** supabase-js does not persist one, does not refresh one,
 *    and `persistSession` / `autoRefreshToken` never apply — there is nothing to persist. The one
 *    durable credential on the machine is the daemon's OS-keychain item.
 * 2. **`supabase.auth` throws on any property access.** That is supabase-js's own behaviour with
 *    this option, and it is the guard that makes a half-switch impossible to ship: `getSession`,
 *    `setSession`, `refreshSession`, `signOut` and `onAuthStateChange` are gone by construction,
 *    not by convention. Identity and lifecycle come from `lib/custodian` instead.
 *
 * Realtime is the one thing that needs a nudge. realtime-js calls the `accessToken` callback on
 * connect and on resubscribe, but **never on a timer** — so a socket held open past a rotation
 * would carry a dead JWT. `subscribeSession` below re-authorises it the moment the daemon says it
 * rotated. That call is part of the contract, not an optimisation.
 */

import { createClient } from "@supabase/supabase-js";
import { getToken, subscribeSession } from "@/lib/custodian";

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
const supabaseKey = import.meta.env.VITE_SUPABASE_PUBLISHABLE_DEFAULT_KEY;

if (!supabaseUrl || !supabaseKey) {
    console.error(
        '[supabase] Missing env vars. Ensure VITE_SUPABASE_URL and ' +
        'VITE_SUPABASE_PUBLISHABLE_DEFAULT_KEY are set at build time.'
    );
}

const supabase = createClient(supabaseUrl ?? '', supabaseKey ?? '', {
    // May be called concurrently and many times — `getToken` memoises and single-flights.
    accessToken: async () => await getToken(),
});

subscribeSession(() => {
    // An account change or sign-out need not be a token-rotation event. Let
    // realtime-js re-read the callback and fence overlapping auth updates.
    void supabase.realtime.setAuth().catch(() => {
        console.warn("Realtime account update failed; reconnect to retry.");
    });
});

export default supabase;
