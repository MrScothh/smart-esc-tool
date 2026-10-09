import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

// base "./": the same build works at a domain's root and under a path, such as a GitHub Pages project site
export default defineConfig({
  base: "./",
  worker: { format: "es" },
  build: { target: "es2022" },
  plugins: [
    VitePWA({
      // a new version waits until every window is closed: taking over a page that is running would mix two builds
      registerType: "prompt",
      includeAssets: ["icon.png"],
      manifest: {
        name: "Smart ESC Tool",
        short_name: "Smart ESC",
        description:
          "Read and change a Spektrum Avian ESC's settings over SRXL2, through a flight controller running INAV " +
          "or the SRXL2 adapter.",
        theme_color: "#202020",
        background_color: "#202020",
        display: "standalone",
        icons: [
          { src: "icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "icon-512.png", sizes: "512x512", type: "image/png" },
          { src: "icon-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
        ],
      },
      workbox: { globPatterns: ["**/*.{js,css,html,png,svg}"] },
    }),
  ],
});
