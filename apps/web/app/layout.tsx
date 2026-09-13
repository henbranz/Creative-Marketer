import type { Metadata } from "next";
import type { ReactNode } from "react";

import "../src/ui/tokens.css";
import "./styles.css";

export const metadata: Metadata = {
  title: "Creative Manager",
  description: "Creative intelligence for modern growth.",
  icons: {
    icon: "/brand/creative-manager-app-icon.png",
    apple: "/brand/creative-manager-app-icon.png",
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
