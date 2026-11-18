import { DM_Mono, Geist, Geist_Mono, Rethink_Sans, Source_Serif_4, STIX_Two_Text } from "next/font/google";
import "./globals.css";

const geist = Geist({ subsets: ["latin"], variable: "--font-geist", weight: ["400", "500", "700"] });
const geistMono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono", weight: ["400", "500"] });
// Used only by /run. preload:false means ledger pages never download them.
const display = Source_Serif_4({ subsets: ["latin"], variable: "--font-axm-display", weight: ["400", "600"], style: ["normal", "italic"], preload: false });
const sans = Rethink_Sans({ subsets: ["latin"], variable: "--font-axm-sans", weight: ["400", "500", "600"], preload: false });
const mono = DM_Mono({ subsets: ["latin"], variable: "--font-axm-mono", weight: ["400"], preload: false });
const math = STIX_Two_Text({ subsets: ["latin"], variable: "--font-axm-math", weight: ["400"], style: ["normal", "italic"], preload: false });

export const metadata = {
  title: { default: "ICP Qualifier review queue", template: "%s · ICP Qualifier" },
  description: "Read-only review queue for the Sprinto ICP Qualifier: every decision with its evidence, contact and draft.",
};
export const viewport = { width: "device-width", initialScale: 1, themeColor: "#F7F6F0" };

export default function RootLayout({ children }) {
  return (
    <html lang="en" className={`${geist.variable} ${geistMono.variable} ${display.variable} ${sans.variable} ${mono.variable} ${math.variable}`}>
      <body>{children}</body>
    </html>
  );
}
