import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  agentRules: false,
  output: "standalone",
  reactStrictMode: true,
  transpilePackages: ["@creative-marketer/contracts"],
};

export default nextConfig;
