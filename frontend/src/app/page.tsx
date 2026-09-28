"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

type RoleType = "student" | "teacher" | null;

export default function Home() {
  const router = useRouter();
  const [selectedRole, setSelectedRole] = useState<RoleType>(null);
  const [isLoginMode, setIsLoginMode] = useState(true);

  // 表單狀態
  const [formData, setFormData] = useState({
    name: "",
    email: "",
    password: "",
  });

  const handleSelectRole = (role: RoleType) => {
    setSelectedRole(role);
    setIsLoginMode(true);
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const { name, value } = e.target;
    setFormData((prev) => ({ ...prev, [name]: value }));
  };

  // 登入送出處理（轉跳到對應的獨立路由）
  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (selectedRole === "student") {
      router.push("/student/modules");
    } else {
      router.push("/admin/tools");
    }
  };

  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", backgroundColor: "#0f172a", color: "#f8fafc", fontFamily: "sans-serif", padding: "24px" }}>
      {/* 平台標題 */}
      <div style={{ textAlign: "center", marginBottom: "32px" }}>
        <span style={{ padding: "4px 12px", fontSize: "12px", borderRadius: "9999px", backgroundColor: "rgba(255,255,255,0.08)", border: "1px solid rgba(255,255,255,0.15)", color: "#a5b4fc" }}>
          學前特教與早療・AI 培訓平台
        </span>
        <h1 style={{ fontSize: "32px", fontWeight: "bold", margin: "16px 0 8px 0", color: "#ffffff" }}>
          專業能力情境化訓練系統
        </h1>
        <p style={{ fontSize: "14px", color: "#94a3b8", margin: 0 }}>
          {selectedRole === null ? "請選擇您的身分通道開始登入" : `目前入口：${selectedRole === "student" ? "學生登入" : "教師/工作人員登入"}`}
        </p>
      </div>

      {/* 狀態 A：雙卡片選擇入口 */}
      {selectedRole === null && (
        <div style={{ display: "flex", gap: "24px", flexWrap: "wrap", justifyContent: "center" }}>
          {/* 學生卡片 */}
          <div
            onClick={() => handleSelectRole("student")}
            style={{ width: "220px", height: "260px", backgroundColor: "#1e3a8a", border: "2px solid #3b82f6", borderRadius: "24px", display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center", cursor: "pointer", transition: "transform 0.2s" }}
          >
            <div style={{ width: "50px", height: "50px", borderRadius: "16px", backgroundColor: "rgba(255,255,255,0.15)", display: "flex", alignItems: "center", justifyContent: "center", marginBottom: "16px" }}>
              🎓
            </div>
            <span style={{ fontSize: "24px", fontWeight: "bold", color: "#fff" }}>學生</span>
            <span style={{ fontSize: "12px", color: "#93c5fd", marginTop: "8px" }}>IEP 逐步撰寫練習</span>
          </div>

          {/* 教師卡片 */}
          <div
            onClick={() => handleSelectRole("teacher")}
            style={{ width: "220px", height: "260px", backgroundColor: "#14532d", border: "2px solid #22c55e", borderRadius: "24px", display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center", cursor: "pointer", transition: "transform 0.2s" }}
          >
            <div style={{ width: "50px", height: "50px", borderRadius: "16px", backgroundColor: "rgba(255,255,255,0.15)", display: "flex", alignItems: "center", justifyContent: "center", marginBottom: "16px" }}>
              🧑‍🏫
            </div>
            <span style={{ fontSize: "24px", fontWeight: "bold", color: "#fff" }}>教師/人員</span>
            <span style={{ fontSize: "12px", color: "#86efac", marginTop: "8px" }}>後台複核・分析</span>
          </div>
        </div>
      )}

      {/* 狀態 B：信箱登入/註冊表單 */}
      {selectedRole !== null && (
        <div style={{ width: "100%", maxWidth: "380px", backgroundColor: "#1e293b", borderRadius: "24px", border: "1px solid rgba(255,255,255,0.15)", padding: "28px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" }}>
            <span style={{ fontSize: "12px", padding: "3px 10px", borderRadius: "9999px", backgroundColor: selectedRole === "student" ? "#2563eb" : "#16a34a", color: "#fff" }}>
              ● {selectedRole === "student" ? "學生登入" : "教師登入"}
            </span>
            <button
              onClick={() => setSelectedRole(null)}
              style={{ fontSize: "12px", color: "#94a3b8", background: "none", border: "none", cursor: "pointer" }}
            >
              切換身分 ↺
            </button>
          </div>

          <h2 style={{ fontSize: "18px", fontWeight: "bold", margin: "0 0 16px 0" }}>
            {isLoginMode ? "登入您的帳號" : "註冊新帳號"}
          </h2>

          <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
            {!isLoginMode && (
              <input
                type="text"
                name="name"
                required
                placeholder="姓名 / 稱呼"
                value={formData.name}
                onChange={handleChange}
                style={{ padding: "10px 12px", borderRadius: "8px", border: "1px solid rgba(255,255,255,0.2)", backgroundColor: "rgba(255,255,255,0.05)", color: "#fff", fontSize: "14px", outline: "none" }}
              />
            )}

            <input
              type="email"
              name="email"
              required
              placeholder="電子信箱 (Email)"
              value={formData.email}
              onChange={handleChange}
              style={{ padding: "10px 12px", borderRadius: "8px", border: "1px solid rgba(255,255,255,0.2)", backgroundColor: "rgba(255,255,255,0.05)", color: "#fff", fontSize: "14px", outline: "none" }}
            />

            <input
              type="password"
              name="password"
              required
              placeholder="密碼"
              value={formData.password}
              onChange={handleChange}
              style={{ padding: "10px 12px", borderRadius: "8px", border: "1px solid rgba(255,255,255,0.2)", backgroundColor: "rgba(255,255,255,0.05)", color: "#fff", fontSize: "14px", outline: "none" }}
            />

            <button
              type="submit"
              style={{ marginTop: "8px", padding: "10px", borderRadius: "8px", border: "none", backgroundColor: selectedRole === "student" ? "#2563eb" : "#16a34a", color: "#fff", fontWeight: "bold", fontSize: "14px", cursor: "pointer" }}
            >
              {isLoginMode ? "登入並進入系統" : "建立帳號"}
            </button>
          </form>

          <div style={{ marginTop: "16px", textAlign: "center", fontSize: "12px", color: "#94a3b8" }}>
            <button
              onClick={() => setIsLoginMode(!isLoginMode)}
              style={{ background: "none", border: "none", color: "#38bdf8", cursor: "pointer", textDecoration: "underline" }}
            >
              {isLoginMode ? "還沒有帳號？前往註冊 →" : "← 已有帳號？返回登入"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}