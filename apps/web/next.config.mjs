/** Static export: the whole UI is plain files served by Cloudflare Pages (free).
 *  Data comes from Supabase (RLS) and the Modal API. */
const nextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  reactStrictMode: true,
};

export default nextConfig;
