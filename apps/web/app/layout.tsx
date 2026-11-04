import type { Metadata } from "next";
import type { ReactNode } from "react";

import "../src/ui/tokens.css";
import "./styles.css";
import "../src/ui/workbench.css";

export const metadata: Metadata = {
  title: "Creative Manager",
  description: "Creative intelligence for modern growth.",
  icons: {
    icon: {
      url: "/brand/creative-manager-app-icon.svg",
      type: "image/svg+xml",
    },
  },
};

export default function RootLayout({
  children,
}: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
