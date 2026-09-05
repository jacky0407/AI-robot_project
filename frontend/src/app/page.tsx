"use client";

import { useState } from "react";

type RoleType = "student" | "teacher" | null;
type ViewState = "auth" | "student_workspace" | "teacher_dashboard";

export default function Home() {
  const [selectedRole, setSelectedRole] = useState<RoleType>(null);
  const [isLoginMode, setIsLoginMode] = useState(true);
  const [currentView, setCurrentView] = useState<ViewState>("auth");

  // 表單狀態
  const [formData, setFormData] = useState({
    name: "",
    email: "",
    password: "",
    profession: "preschool_teacher",
    agreeTerms: false,
    agreePrivacy: false,
  });

  // 學生工作區的練習狀態
  const [currentStep, setCurrentStep] = useState(2);
  const [studentAnswer, setStudentAnswer] = useState("");
  const [unlockedPrompt, setUnlockedPrompt] = useState(1);
  const [evalResult, setEvalResult] = useState<any>(null);

  const handleSelectRole = (role: RoleType) => {
    setSelectedRole(role);
    setIsLoginMode(true);
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    const { name, value, type } = e.target;
    if (type === "checkbox") {
      const checked = (e.target as HTMLInputElement).checked;
      setFormData((prev) => ({ ...prev, [name]: checked }));
    } else {
      setFormData((prev) => ({ ...prev, [name]: value }));
    }
  };

  // 模擬一般登入成功
  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (selectedRole === "student") {
      setCurrentView("student_workspace");
    } else {
      setCurrentView("teacher_dashboard");
    }
  };

  // ==========================================
  // 畫面 1：學生練習工作區 (跳過驗證或登入後)
  // ==========================================
  if (currentView === "student_workspace") {
    return (
      <div style={{ minHeight: "100vh", backgroundColor: "#f8fafc", color: "#1e293b", fontFamily: "sans-serif" }}>
        {/* 頂部導航 */}
        <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "12px 24px", backgroundColor: "#fff", borderBottom: "1px solid #e2e8f0" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            <span style={{ backgroundColor: "#2563eb", color: "#fff", padding: "4px 8px", borderRadius: "6px", fontSize: "12px", fontWeight: "bold" }}>
              學生實踐端
            </span>
            <span style={{ fontWeight: "bold", fontSize: "15px" }}>學前 IEP 逐步撰寫：案例 小宇（自閉症特質）</span>
          </div>
          <button
            onClick={() => { setCurrentView("auth"); setSelectedRole(null); }}
            style={{ padding: "6px 12px", fontSize: "12px", border: "1px solid #cbd5e1", borderRadius: "6px", cursor: "pointer", backgroundColor: "#fff" }}
          >
            ← 登出 / 返回首頁
          </button>
        </header>

        {/* 步驟橫條 */}
        <div style={{ display: "flex", gap: "8px", padding: "10px 24px", backgroundColor: "#fff", borderBottom: "1px solid #e2e8f0", overflowX: "auto" }}>
          {["1.案例整理", "2.功能性現況", "3.優勢與需求", "4.優先排序", "5.年度目標", "6.短期目標", "7.支持策略", "8.評量監測", "9.一致性檢核"].map((step, idx) => (
            <button
              key={step}
              onClick={() => setCurrentStep(idx + 1)}
              style={{
                padding: "6px 12px",
                borderRadius: "6px",
                border: "none",
                fontSize: "12px",
                cursor: "pointer",
                fontWeight: currentStep === idx + 1 ? "bold" : "normal",
                backgroundColor: currentStep === idx + 1 ? "#2563eb" : "#f1f5f9",
                color: currentStep === idx + 1 ? "#fff" : "#475569"
              }}
            >
              {step}
            </button>
          ))}
        </div>

        {/* 工作區主體 */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1.5fr 1fr", gap: "16px", padding: "16px", maxWidth: "1400px", margin: "0 auto" }}>
          {/* 左：案例情境 */}
          <div style={{ backgroundColor: "#fff", borderRadius: "12px", padding: "16px", border: "1px solid #e2e8f0" }}>
            <h3 style={{ fontSize: "14px", fontWeight: "bold", borderBottom: "1px solid #e2e8f0", paddingBottom: "8px", margin: "0 0 12px 0" }}>
              步驟 {currentStep} 案例資料
            </h3>
            <p style={{ fontSize: "13px", lineHeight: "1.6", color: "#475569" }}>
              <strong>幼兒基本資料：</strong>小宇，4歲2個月。<br /><br />
              <strong>日常情境觀察：</strong>角落時間常獨自排列積木，他人拿取時會推人；點心時間能自行用湯匙進食，但若未按固定座位入座會尖叫。<br /><br />
              <strong style={{ color: "#2563eb" }}>本步驟任務：</strong>請撰寫小宇在自然情境中的「功能性現況」初稿。
            </p>
          </div>

          {/* 中：獨立作答 */}
          <div style={{ backgroundColor: "#fff", borderRadius: "12px", padding: "16px", border: "1px solid #e2e8f0", display: "flex", flexDirection: "column" }}>
            <h3 style={{ fontSize: "14px", fontWeight: "bold", margin: "0 0 12px 0" }}>學生獨立作答區</h3>
            <textarea
              rows={12}
              value={studentAnswer}
              onChange={(e) => setStudentAnswer(e.target.value)}
              placeholder="請輸入現況描述（例：小宇在點心與角落情境下的參與度與支持需求...）"
              style={{ width: "100%", padding: "12px", borderRadius: "8px", border: "1px solid #cbd5e1", fontSize: "14px", outline: "none", resize: "none" }}
            />
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "12px" }}>
              <span style={{ fontSize: "12px", color: "#64748b" }}>字數：{studentAnswer.length} 字</span>
              <button
                onClick={() => {
                  setEvalResult({
                    score: 85,
                    feedback: "已能客觀陳述自然情境，建議具體指出視覺提示之介入方式。",
                    evidence: `依作答：「${studentAnswer.slice(0, 25)}...」`
                  });
                }}
                style={{ padding: "8px 16px", backgroundColor: "#2563eb", color: "#fff", border: "none", borderRadius: "8px", fontWeight: "bold", cursor: "pointer", fontSize: "13px" }}
              >
                送出並進行 AI 初評
              </button>
            </div>
          </div>

          {/* 右：分層提示與 AI 回饋 */}
          <div style={{ backgroundColor: "#fff", borderRadius: "12px", padding: "16px", border: "1px solid #e2e8f0" }}>
            <h3 style={{ fontSize: "14px", fontWeight: "bold", borderBottom: "1px solid #e2e8f0", paddingBottom: "8px", margin: "0 0 12px 0" }}>
              AI 規準分析與分層提示
            </h3>
            {evalResult ? (
              <div style={{ backgroundColor: "#eff6ff", border: "1px solid #bfdbfe", padding: "12px", borderRadius: "8px", marginBottom: "16px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontWeight: "bold", color: "#1e3a8a", fontSize: "13px" }}>
                  <span>AI 證據初評</span>
                  <span>{evalResult.score} 分</span>
                </div>
                <p style={{ fontSize: "12px", margin: "8px 0", color: "#334155" }}>{evalResult.feedback}</p>
                <span style={{ fontSize: "11px", color: "#64748b", fontStyle: "italic" }}>{evalResult.evidence}</span>
              </div>
            ) : (
              <p style={{ fontSize: "12px", color: "#94a3b8" }}>送出作答後將產生分析回饋。</p>
            )}

            <div style={{ marginTop: "12px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                <span style={{ fontSize: "12px", fontWeight: "bold" }}>分層提示（第 {unlockedPrompt}/3 層）</span>
                {unlockedPrompt < 3 && (
                  <button
                    onClick={() => setUnlockedPrompt(p => p + 1)}
                    style={{ fontSize: "11px", color: "#2563eb", border: "none", background: "none", cursor: "pointer", textDecoration: "underline" }}
                  >
                    展開下一層提示
                  </button>
                )}
              </div>
              <div style={{ fontSize: "12px", backgroundColor: "#f8fafc", padding: "8px", borderRadius: "6px", border: "1px solid #e2e8f0", lineHeight: "1.5" }}>
                {unlockedPrompt === 1 && "第 1 層【重新思考】：請確認描述是否包含幼兒的參與度而非僅有情緒反應？"}
                {unlockedPrompt === 2 && "第 2 層【方向提示】：建議寫出幼兒能獨立完成的部分，以及需要成人介入支持的具體時機。"}
                {unlockedPrompt === 3 && "第 3 層【結構提示】：建議採用「在 [作息活動] 中，幼兒能 [現有能力]，但在 [困難節點] 時需要 [支持方式]」之句型。"}
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ==========================================
  // 畫面 2：教師與後台儀表板 (跳過驗證或登入後)
  // ==========================================
  if (currentView === "teacher_dashboard") {
    return (
      <div style={{ minHeight: "100vh", backgroundColor: "#f8fafc", color: "#1e293b", fontFamily: "sans-serif" }}>
        <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "14px 24px", backgroundColor: "#fff", borderBottom: "1px solid #e2e8f0" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            <span style={{ backgroundColor: "#16a34a", color: "#fff", padding: "4px 8px", borderRadius: "6px", fontSize: "12px", fontWeight: "bold" }}>
              管理與複核後台
            </span>
            <span style={{ fontWeight: "bold", fontSize: "16px" }}>培訓分析儀表板</span>
          </div>
          <button
            onClick={() => { setCurrentView("auth"); setSelectedRole(null); }}
            style={{ padding: "6px 12px", fontSize: "12px", border: "1px solid #cbd5e1", borderRadius: "6px", cursor: "pointer", backgroundColor: "#fff" }}
          >
            ← 登出 / 返回首頁
          </button>
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

  // ==========================================
  // 畫面 0：身分選擇與登入首頁（含快速測試通道）
  // ==========================================
  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", backgroundColor: "#0f172a", color: "#f8fafc", fontFamily: "sans-serif", padding: "24px" }}>
      
      {/* 🚀 開發者免驗證測試快捷列 */}
      <div style={{ marginBottom: "28px", padding: "12px 20px", backgroundColor: "rgba(30, 41, 59, 0.9)", border: "1px dashed #38bdf8", borderRadius: "16px", display: "flex", alignItems: "center", gap: "12px", boxShadow: "0 4px 20px rgba(56, 189, 248, 0.15)" }}>
        <span style={{ fontSize: "12px", fontWeight: "bold", color: "#38bdf8" }}>⚡ 開發測試專用捷徑：</span>
        <button
          onClick={() => setCurrentView("student_workspace")}
          style={{ padding: "6px 14px", backgroundColor: "#2563eb", color: "#fff", border: "none", borderRadius: "8px", fontSize: "12px", fontWeight: "bold", cursor: "pointer" }}
        >
          直接進入【學生工作區】
        </button>
        <button
          onClick={() => setCurrentView("teacher_dashboard")}
          style={{ padding: "6px 14px", backgroundColor: "#16a34a", color: "#fff", border: "none", borderRadius: "8px", fontSize: "12px", fontWeight: "bold", cursor: "pointer" }}
        >
          直接進入【教師後台】
        </button>
      </div>

      {/* 平台標題 */}
      <div style={{ textAlign: "center", marginBottom: "32px" }}>
        <span style={{ padding: "4px 12px", fontSize: "12px", borderRadius: "9999px", backgroundColor: "rgba(255,255,255,0.08)", border: "1px solid rgba(255,255,255,0.15)", color: "#a5b4fc" }}>
          學前特教與早療・AI 培訓平台
        </span>
        <h1 style={{ fontSize: "32px", fontWeight: "bold", margin: "16px 0 8px 0", color: "#ffffff" }}>
          專業能力情境化訓練系統
        </h1>
        <p style={{ fontSize: "14px", color: "#94a3b8", margin: 0 }}>
          {selectedRole === null ? "請選擇您的身分通道開始練習" : `目前身分：${selectedRole === "student" ? "學生" : "教師/工作人員"}`}
        </p>
      </div>

      {/* 狀態 A：雙卡片選擇 */}
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