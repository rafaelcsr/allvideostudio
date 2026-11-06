/** Static export: the whole UI is plain files served by Cloudflare Pages (free).
 *  Data comes from Supabase (RLS) and the Modal API. */

// Security gate: NEXT_PUBLIC_* values are embedded in the public JS bundle.
// Abort the build if any of them holds a privileged credential.
function assertNoSecretsInPublicEnv() {
  const roleOf = (jwt) => {
    try {
      return JSON.parse(Buffer.from(jwt.split(".")[1], "base64url").toString()).role;
    } catch {
      return undefined;
    }
  };
  for (const [name, value] of Object.entries(process.env)) {
    if (!name.startsWith("NEXT_PUBLIC_") || !value) continue;
    const secret =
      value.startsWith("sb_secret_") ||
      (value.startsWith("eyJ") && roleOf(value) === "service_role") ||
      /sk-ant-|R2_SECRET|postgres(ql)?:\/\/[^@]+:[^@]+@/i.test(value);
    if (secret) {
      throw new Error(`BUILD BLOQUEADO: ${name} contém uma credencial privilegiada. Use apenas a chave publishable/anon.`);
    }
  }
}
assertNoSecretsInPublicEnv();

const nextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  reactStrictMode: true,
};

export default nextConfig;
