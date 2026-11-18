import { redirect } from "next/navigation";

// 監控總覽已併入課程管理：每門課裡的「學習監控」分頁。舊網址（書籤、文件連結）導到課程列表
export default function Moved() {
  redirect("/admin/courses");
}
