"use client";

import Link from "next/link";

export default function AdminToolsPage() {
  return (
    <div style={{ minHeight: "100vh", backgroundColor: "#f8fafc", color: "#1e293b", fontFamily: "sans-serif" }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "14px 24px", backgroundColor: "#fff", borderBottom: "1px solid #e2e8f0" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          <span style={{ backgroundColor: "#16a34a", color: "#fff", padding: "4px 8px", borderRadius: "6px", fontSize: "12px", fontWeight: "bold" }}>
            管理與複核後台
          </span>
          <span style={{ fontWeight: "bold", fontSize: "16px" }}>培訓分析儀表板</span>
        </div>
        <Link
          href="/"
          style={{ padding: "6px 12px", fontSize: "12px", border: "1px solid #cbd5e1", borderRadius: "6px", cursor: "pointer", backgroundColor: "#fff", textDecoration: "none", color: "#1e293b" }}
        >
          ← 登出 / 返回首頁
        </Link>
      </header>

      <div style={{ maxWidth: "1200px", margin: "24px auto", padding: "0 16px", display: "grid", gridTemplateColumns: "2fr 1fr", gap: "20px" }}>
        {/* 左：待複核清單 */}
        <div style={{ backgroundColor: "#fff", borderRadius: "12px", padding: "20px", border: "1px solid #e2e8f0" }}>
          <h3 style={{ fontSize: "15px", fontWeight: "bold", margin: "0 0 16px 0" }}>待複核學生初評清單 (需求書 ASM-002)</h3>
          <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
            {[
              { name: "王同學", task: "IEP 步驟 2 功能性現況", aiScore: 82, time: "10 分鐘前" },
              { name: "李同學", task: "IEP 步驟 5 年度目標", aiScore: 74, time: "35 分鐘前" },
              { name: "張學員", task: "IEP 步驟 7 支持策略", aiScore: 90, time: "1 小時前" },
            ].map((item, i) => (
              <div key={i} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "12px", backgroundColor: "#f8fafc", borderRadius: "8px", border: "1px solid #e2e8f0" }}>
                <div>
                  <span style={{ fontWeight: "bold", fontSize: "14px" }}>{item.name}</span>
                  <span style={{ fontSize: "12px", color: "#64748b", marginLeft: "10px" }}>{item.task}</span>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                  <span style={{ fontSize: "12px", backgroundColor: "#dbeafe", color: "#1e40af", padding: "2px 8px", borderRadius: "4px" }}>AI 初評: {item.aiScore}分</span>
                  <button
                    onClick={() => alert(`進入複核 ${item.name} 的作答與 AI 初評結果`)}
                    style={{ padding: "4px 10px", fontSize: "12px", backgroundColor: "#16a34a", color: "#fff", border: "none", borderRadius: "6px", cursor: "pointer" }}
                  >
                    審核判定
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* 右：快速工具與統計 */}
        <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          <div style={{ backgroundColor: "#fff", borderRadius: "12px", padding: "20px", border: "1px solid #e2e8f0" }}>
            <h3 style={{ fontSize: "14px", fontWeight: "bold", margin: "0 0 12px 0" }}>AI 工具建構器捷徑</h3>
            <p style={{ fontSize: "12px", color: "#64748b", margin: "0 0 12px 0" }}>主持人可在此新增或修訂 AI 能力機器人與 Rubric（無需工程師改程式碼）。</p>
            <button
              onClick={() => alert("開啟 AI 工具建構器（設定 System Prompt、Rubric、分層提示）")}
              style={{ width: "100%", padding: "8px 0", backgroundColor: "#0f172a", color: "#fff", border: "none", borderRadius: "6px", fontSize: "13px", fontWeight: "bold", cursor: "pointer" }}
            >
              + 建立 / 編輯 AI 機器人
            </button>
          </div>

          <div style={{ backgroundColor: "#fff", borderRadius: "12px", padding: "20px", border: "1px solid #e2e8f0" }}>
            <h3 style={{ fontSize: "14px", fontWeight: "bold", margin: "0 0 12px 0" }}>研究資料匯出 (DAT-001)</h3>
            <p style={{ fontSize: "12px", color: "#64748b", margin: "0 0 12px 0" }}>支援去識別化學習歷程資料 CSV 匯出。</p>
            <button
              onClick={() => alert("匯出研究去識別資料成功（包含版本、對話、提示使用時間與分數）")}
              style={{ width: "100%", padding: "8px 0", border: "1px solid #cbd5e1", backgroundColor: "#fff", color: "#334155", borderRadius: "6px", fontSize: "13px", cursor: "pointer" }}
            >
              匯出去識別化研究數據
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}