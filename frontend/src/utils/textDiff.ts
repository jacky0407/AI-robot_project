// 兩段作答文字的差異（作答版本比對用）
//
// 中文沒有空白分詞，所以以「字」為單位比對；英文單字、數字、連續空白各算一個單位，
// 避免 "ABC" 被拆成三個字元而出現零碎的標示。
// 演算法：最長共同子序列（LCS）。作答通常幾百到一兩千字，O(n×m) 足夠；
// 超過上限時改用「以行為單位」比對，避免瀏覽器卡住。

export type DiffPart = { type: "same" | "added" | "removed"; text: string };

const TOKEN_RE = /[A-Za-z0-9_]+|\s+|[\s\S]/gu;
const MAX_CELLS = 4_000_000; // n×m 超過這個數量改用逐行比對

export function tokenize(text: string): string[] {
  return text.match(TOKEN_RE) ?? [];
}

function lcsDiff(a: string[], b: string[]): DiffPart[] {
  const n = a.length;
  const m = b.length;
  // dp[i][j] = a[i..] 與 b[j..] 的 LCS 長度，用一維陣列存 (n+1)×(m+1)
  const width = m + 1;
  const dp = new Uint32Array((n + 1) * width);
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i * width + j] =
        a[i] === b[j] ? dp[(i + 1) * width + j + 1] + 1 : Math.max(dp[(i + 1) * width + j], dp[i * width + j + 1]);
    }
  }

  const parts: DiffPart[] = [];
  const push = (type: DiffPart["type"], text: string) => {
    const last = parts[parts.length - 1];
    if (last && last.type === type) last.text += text;
    else parts.push({ type, text });
  };

  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      push("same", a[i]);
      i++;
      j++;
    } else if (dp[(i + 1) * width + j] >= dp[i * width + j + 1]) {
      push("removed", a[i++]);
    } else {
      push("added", b[j++]);
    }
  }
  while (i < n) push("removed", a[i++]);
  while (j < m) push("added", b[j++]);
  return parts;
}

export function diffText(oldText: string, newText: string): DiffPart[] {
  if (oldText === newText) return oldText ? [{ type: "same", text: oldText }] : [];

  const a = tokenize(oldText);
  const b = tokenize(newText);

  // 先去掉頭尾相同的部分，只比對中間改過的地方（修改通常是局部的，長文也能逐字比對）
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA--;
    endB--;
  }
  const midA = a.slice(start, endA);
  const midB = b.slice(start, endB);
  if (midA.length * midB.length <= MAX_CELLS) {
    const parts: DiffPart[] = [];
    const head = a.slice(0, start).join("");
    const tail = a.slice(endA).join("");
    if (head) parts.push({ type: "same", text: head });
    for (const part of lcsDiff(midA, midB)) {
      const last = parts[parts.length - 1];
      if (last && last.type === part.type) last.text += part.text;
      else parts.push({ ...part });
    }
    if (tail) {
      const last = parts[parts.length - 1];
      if (last && last.type === "same") last.text += tail;
      else parts.push({ type: "same", text: tail });
    }
    return parts;
  }

  // 太長：以行為單位比對（每行保留換行）
  const lines = (s: string) => s.match(/[^\n]*\n|[^\n]+$/g) ?? [];
  const la = lines(oldText);
  const lb = lines(newText);
  if (la.length * lb.length <= MAX_CELLS) return lcsDiff(la, lb);
  return [
    { type: "removed", text: oldText },
    { type: "added", text: newText },
  ];
}

// 新增／刪除了幾個字（不算空白），給摘要用
export function diffStats(parts: DiffPart[]): { added: number; removed: number } {
  const count = (t: string) => t.replace(/\s/g, "").length;
  return parts.reduce(
    (acc, p) => {
      if (p.type === "added") acc.added += count(p.text);
      if (p.type === "removed") acc.removed += count(p.text);
      return acc;
    },
    { added: 0, removed: 0 }
  );
}
