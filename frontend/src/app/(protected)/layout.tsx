"use client";
import type { ReactNode } from "react";
import { usePathname } from "next/navigation";
import { AuthGuard } from "@/features/auth/provider";
import { AppShell } from "@/components/shell";

export default function Layout({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const immersive = pathname.startsWith("/workspaces/") && !pathname.includes("/invitations/");
  return <AuthGuard><AppShell immersive={immersive}>{children}</AppShell></AuthGuard>;
}
