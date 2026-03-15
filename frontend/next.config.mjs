/** @type {import('next').NextConfig} */

const extraOrigins = (process.env.ALLOWED_DEV_ORIGINS || "")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);

const nextConfig = {
  allowedDevOrigins: [
    "*.ngrok-free.dev",
    "*.ngrok.io",
    ...extraOrigins,
  ],
  webpack: (config, { dev }) => {
    if (dev) {
      // Disable webpack's persistent cache in development to avoid packfile OOMs
      // on low-memory Windows setups.
      config.cache = false;
    }
    return config;
  },
};

export default nextConfig;
