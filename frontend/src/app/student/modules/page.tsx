"use client";

import { useState } from "react";
import Link from "next/link";

export default function StudentModulesPage() {
  const [currentStep, setCurrentStep] = useState(2);
  const [studentAnswer, setStudentAnswer] = useState("");
  const [unlockedPrompt, setUnlockedPrompt] = useState(1);
  const [evalResult, setEvalResult] = useState<any>(null);

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
        <Link
          href="/"
          style={{ padding: "6px 12px", fontSize: "12px", border: "1px solid #cbd5e1", borderRadius: "6px", cursor: "pointer", backgroundColor: "#fff", textDecoration: "none", color: "#1e293b" }}
        >
          ← 登出 / 返回首頁
        </Link>
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
                  onClick={() => setUnlockedPrompt((p) => p + 1)}
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