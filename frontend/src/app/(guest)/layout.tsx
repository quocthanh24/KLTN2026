import type { ReactNode } from "react";
import { AuthGuard } from "@/features/auth/provider";
import { AuthShell } from "@/components/shell";

export default function Layout({ children }: { children: ReactNode }) {
  return <AuthGuard guest><AuthShell>{children}</AuthShell></AuthGuard>;
}
