"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiFailure } from "@/lib/api";
import { EmptyState, RoleBadge } from "@/components/shell";
import { useAuth } from "@/features/auth/provider";
import { workspaceServices as api } from "@/features/auth/store";
import type { Workspace } from "@/lib/workspace-types";

export function Dashboard() {
  const { user } = useAuth();
  const [workspaces, setWorkspaces] = useState<Workspace[] | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    api.list().then(result => { if (active) setWorkspaces(result); }).catch(failure => {
      if (active) setError(failure instanceof ApiFailure ? failure.message : "Không thể tải workspace.");
    });
    return () => { active = false; };
  }, [attempt]);
  return (
    <>
      <div className="page-head">
        <div>
          <p className="kicker">Bảng điều khiển</p>
          <h1>Xin chào, {user?.full_name}</h1>
          <p className="lede">Vào workspace để chat nhóm, mở phòng tự học hoặc mời thành viên.</p>
        </div>
        <Link className="button-link" href="/workspaces">Tất cả workspace</Link>
      </div>
      <section className="dash-section">
        <div className="page-head">
          <h2>Workspace gần đây</h2>
        </div>
        {error ? <><p role="alert" className="error">{error}</p><button type="button" onClick={() => { setError(""); setWorkspaces(null); setAttempt(n => n + 1); }}>Thử lại</button></>
          : !workspaces ? <p role="status">Đang tải workspace…</p>
          : workspaces.length ? (
            <div className="grid-cards">{workspaces.slice(0, 6).map(workspace => (
              <Link className="workspace-card" key={workspace.id} href={`/workspaces/${workspace.id}`}>
                <h2>{workspace.name}</h2>
                <p>{workspace.description || "Chưa có mô tả."}</p>
                <RoleBadge role={workspace.role} />
              </Link>
            ))}</div>
          ) : <EmptyState title="Chưa có workspace">Tạo workspace mới hoặc mở danh sách để nhập mã lời mời.</EmptyState>}
      </section>
      <div className="grid-cards">
        <Link className="card" href="/workspaces">
          <p className="kicker">Cộng tác</p>
          <h2>Workspace</h2>
          <p>Tạo nhóm học, kênh chat và phòng tự học ảo.</p>
        </Link>
        <Link className="card" href="/profile">
          <p className="kicker">Tài khoản</p>
          <h2>Thông tin cá nhân</h2>
          <p>Cập nhật họ tên hiển thị trong lớp và phòng học.</p>
        </Link>
        <Link className="card" href="/settings/security">
          <p className="kicker">Bảo mật</p>
          <h2>Đổi mật khẩu</h2>
          <p>Phiên hiện tại được giữ; các phiên khác sẽ đăng xuất.</p>
        </Link>
        <div className="card">
          <p className="kicker">Sắp có</p>
          <h2>Trợ lý tài liệu</h2>
          <p>Hỏi đáp tài liệu học tập bằng AI sẽ được gắn vào workspace ở giai đoạn sau.</p>
        </div>
      </div>
    </>
  );
}
