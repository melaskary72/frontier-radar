/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  devIndicators: false,
  // Separate output folder lets a production build run while `next dev` is serving.
  distDir: process.env.NEXT_DIST_DIR || ".next",
};
export default nextConfig;
