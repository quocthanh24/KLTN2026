"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { useSubmission } from "@/components/forms";
import { useAuth } from "@/features/auth/provider";
import { authStore } from "@/features/auth/store";

export function initials(name: string) {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase() || "?";
}

export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <Link href="/app" className="brand">
      <span className="brand-mark" aria-hidden>K</span>
      {!compact && (
        <span className="brand-copy">
          <strong>KLTN</strong>
          <small>Học tập cộng tác</small>
        </span>
      )}
    </Link>
  );
}

const links = [
  { href: "/app", label: "Trang chính" },
  { href: "/workspaces", label: "Workspace" },
  { href: "/learning-analytics", label: "Thống kê học tập" },
  { href: "/profile", label: "Hồ sơ" },
  { href: "/settings/security", label: "Bảo mật" },
];

export function AppShell({ children, immersive = false }: { children: ReactNode; immersive?: boolean }) {
  const pathname = usePathname();
  const { user } = useAuth();
  const submit = useSubmission();
  const router = useRouter();
  return (
    <div className="app-shell">
      <aside className="app-nav">
        <Brand />
        <nav aria-label="Điều hướng chính">
          {links.map(link => (
            <Link key={link.href} href={link.href} aria-current={pathname === link.href || (link.href !== "/app" && pathname.startsWith(link.href)) ? "page" : undefined}>
              {link.label}
            </Link>
          ))}
          {user?.system_role === "ADMIN" && (
            <Link href="/admin" aria-current={pathname.startsWith("/admin") ? "page" : undefined}>Quản trị hệ thống</Link>
          )}
        </nav>
        <div className="app-user">
          <div className="app-user-meta">
            <span className="avatar" aria-hidden>{user ? initials(user.full_name) : "?"}</span>
            <div>
              <p><strong>{user?.full_name}</strong></p>
              <small>{user?.email}</small>
            </div>
          </div>
          <div className="actions">
            <button className="ghost" disabled={submit.pending} onClick={() => void submit.run(async () => { await authStore.logout(); router.replace("/login"); })}>
              {submit.pending ? "Đang đăng xuất…" : "Đăng xuất"}
            </button>
          </div>
        </div>
      </aside>
      <div className={immersive ? "app-content immersive" : "app-content"}>{children}</div>
    </div>
  );
}

export function AuthShell({ children }: { children: ReactNode }) {
  return (
    <div className="auth-shell">
      <section className="auth-hero">
        <Brand />
        <div>
          <p className="kicker" style={{ color: "#f3e2c4" }}>Không gian học tập</p>
          <h1>Học cùng nhau, rõ ràng và tập trung.</h1>
          <p>Workspace, kênh chat, phòng tự học trực tuyến và tài liệu sẽ họp về một nơi — dành cho nhóm học, lớp học và đề tài.</p>
        </div>
        <ul className="auth-points">
          <li>Phòng học ảo với micro, camera và chia sẻ màn hình</li>
          <li>Chat nhóm theo kênh, tệp đính kèm và cảm xúc</li>
          <li>Mời thành viên vào workspace bằng liên kết</li>
        </ul>
      </section>
      <div className="auth-panel">{children}</div>
    </div>
  );
}

export function RoleBadge({ role }: { role: string }) {
  const label = role === "OWNER" ? "Chủ không gian" : role === "ADMIN" ? "Quản trị" : "Thành viên";
  return <span className={`badge ${role === "OWNER" ? "owner" : role === "ADMIN" ? "admin" : ""}`}>{label} · {role}</span>;
}

export function EmptyState({ title, children }: { title: string; children: React.ReactNode }) {
  return <div className="empty"><h2>{title}</h2><p className="lede">{children}</p></div>;
}
