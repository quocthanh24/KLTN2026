import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Be_Vietnam_Pro, Fraunces } from "next/font/google";
import { AuthProvider } from "@/features/auth/provider";
import "./globals.css";

const sans = Be_Vietnam_Pro({ subsets: ["latin", "vietnamese"], weight: ["400", "600", "700"], variable: "--font-sans" });
const serif = Fraunces({ subsets: ["latin", "vietnamese"], weight: ["600", "700"], variable: "--font-serif" });

export const metadata: Metadata = {
  title: "KLTN — Học tập cộng tác",
  description: "Nền tảng học tập cộng tác trực tuyến: workspace, chat và phòng tự học ảo.",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="vi">
      <body className={`${sans.variable} ${serif.variable} ${sans.className}`}>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
