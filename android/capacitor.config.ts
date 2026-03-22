import type { CapacitorConfig } from "@capacitor/cli";

const config: CapacitorConfig = {
  appId: "com.polymarket.panel",
  appName: "Polymarket Panel",
  webDir: "www",
  server: {
    url: "https://www.alcochrom.icu",
    cleartext: false
  },
  android: {
    allowMixedContent: false
  }
};

export default config;
