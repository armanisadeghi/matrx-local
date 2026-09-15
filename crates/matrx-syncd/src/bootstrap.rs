//! The values baked in at build time by `build.rs`.
//!
//! Read once at start and immediately overridable by what the app passes on the command line —
//! SPEC-CUSTODY's "the daemon receives them from the app at start, or falls back to what it was
//! packaged with". Nothing here is read from a `.env` at run time.

/// The Supabase URL this binary was packaged against. The database is addressed **only** by URL,
/// never by project ref.
pub const SUPABASE_URL: &str = env!("MATRX_SYNCD_SUPABASE_URL");

/// The publishable (anon) key this binary was packaged against. Public by design — it is what
/// `apikey` carries on an RLS-protected request, and it is never a secret.
pub const SUPABASE_PUBLISHABLE_KEY: &str = env!("MATRX_SYNCD_SUPABASE_PUBLISHABLE_KEY");
