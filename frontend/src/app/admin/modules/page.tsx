import { redirect } from "next/navigation";

// 模組管理已併入課程管理：每門課裡的「模組管理」分頁。舊網址（書籤、文件連結）導到課程列表
export default function Moved() {
  redirect("/admin/courses");
}
