'use client'

// 教師端兩步驟驗證（#90）：教授、助理進後台前一定要完成
//   - 還沒設定過：顯示 QR code，用手機的驗證器 App（Google Authenticator、Microsoft Authenticator）掃描，
//     輸入 App 上的 6 位數完成設定
//   - 設定過：輸入 App 上當下的 6 位數
// 完成後這次登入的等級變成 aal2，middleware 與後端（core/auth.py 的 require_staff）才會放行。
// 使用 Supabase Auth 內建的兩步驟驗證（TOTP），密鑰存在 Supabase，不經過我們的後端。
// 換手機或手機遺失：由管理者在 Supabase → Authentication → Users 刪掉那位老師的驗證設定，再重新設定。

import { Suspense, useEffect, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { createClient } from '@/utils/supabase/client'
import { safeNext } from '@/utils/redirect'
import { APP_ICON, APP_NAME, APP_NAME_ZH } from '../brand'

type Mode = 'loading' | 'enroll' | 'verify' | 'error'

const card: React.CSSProperties = {
  width: '100%',
  maxWidth: '440px',
  padding: '28px',
  borderRadius: '16px',
  backgroundColor: '#1e293b',
  border: '1px solid rgba(255,255,255,0.1)',
  boxSizing: 'border-box',
}
const hint: React.CSSProperties = { margin: 0, fontSize: '13px', color: '#94a3b8', lineHeight: 1.7 }

export default function MfaPage() {
  return (
    <Suspense fallback={null}>
      <Mfa />
    </Suspense>
  )
}

function Mfa() {
  const router = useRouter()
  const next = safeNext(useSearchParams().get('next')) ?? '/admin/courses'
  const [supabase] = useState(() => createClient())

  const [mode, setMode] = useState<Mode>('loading')
  const [factorId, setFactorId] = useState('')
  const [qrCode, setQrCode] = useState('')
  const [secret, setSecret] = useState('')
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [errorMsg, setErrorMsg] = useState('')

  useEffect(() => {
    let active = true
    const start = async () => {
      const { data, error } = await supabase.auth.mfa.listFactors()
      if (!active) return
      if (error || !data) {
        setErrorMsg(`無法讀取兩步驟驗證設定：${error?.message ?? ''}`)
        setMode('error')
        return
      }

      // 設定過：輸入驗證碼
      const verified = data.totp[0]
      if (verified) {
        setFactorId(verified.id)
        setMode('verify')
        return
      }

      // 還沒設定：先清掉上次沒完成的設定（掃了但沒輸入驗證碼），再產生新的 QR code
      for (const f of data.all) {
        if (f.factor_type === 'totp' && f.status !== 'verified') {
          await supabase.auth.mfa.unenroll({ factorId: f.id })
        }
      }
      const enrolled = await supabase.auth.mfa.enroll({ factorType: 'totp', issuer: APP_NAME, friendlyName: '驗證器 App' })
      if (!active) return
      if (enrolled.error || !enrolled.data) {
        setErrorMsg(
          `無法開始設定：${enrolled.error?.message ?? ''}（請確認 Supabase 已開啟兩步驟驗證 TOTP，或聯絡管理者）`,
        )
        setMode('error')
        return
      }
      setFactorId(enrolled.data.id)
      setQrCode(enrolled.data.totp.qr_code)
      setSecret(enrolled.data.totp.secret)
      setMode('enroll')
    }
    start()
    return () => {
      active = false
    }
  }, [supabase])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!/^\d{6}$/.test(code)) {
      setErrorMsg('請輸入驗證器 App 上的 6 位數字')
      return
    }
    setBusy(true)
    setErrorMsg('')
    const { error } = await supabase.auth.mfa.challengeAndVerify({ factorId, code })
    if (error) {
      setErrorMsg('驗證碼不正確或已過期，請輸入 App 上「目前」顯示的 6 位數字再試一次')
      setCode('')
      setBusy(false)
      return
    }
    // 這次登入已升級為 aal2；重新整理讓 middleware 讀到新的登入狀態
    router.replace(next)
    router.refresh()
  }

  const handleSignOut = async () => {
    await supabase.auth.signOut()
    router.replace('/')
    router.refresh()
  }

  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px 16px', backgroundColor: '#0f172a', color: '#f8fafc', fontFamily: 'sans-serif', colorScheme: 'dark' }}>
      <div style={card}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '18px' }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={APP_ICON} alt="" width={36} height={36} />
          <div>
            <div style={{ fontSize: '15px', fontWeight: 'bold' }}>{APP_NAME_ZH}</div>
            <div style={{ fontSize: '11px', color: '#4ade80' }}>教師端兩步驟驗證</div>
          </div>
        </div>

        {mode === 'loading' && <p style={hint}>載入中…</p>}

        {mode === 'error' && <p style={{ ...hint, color: '#fca5a5' }}>{errorMsg}</p>}

        {mode === 'enroll' && (
          <>
            <h1 style={{ fontSize: '18px', margin: '0 0 8px' }}>設定兩步驟驗證</h1>
            <p style={hint}>為了保護學生資料，教師端第一次使用前要完成設定，只需要做一次。</p>
            <ol style={{ ...hint, paddingLeft: '20px', margin: '12px 0' }}>
              <li>在手機安裝「Google Authenticator」或「Microsoft Authenticator」（免費）。</li>
              <li>打開 App，按「＋」→「掃描 QR code」，掃描下面的圖。</li>
              <li>把 App 顯示的 6 位數字輸入下方，按「完成設定」。</li>
            </ol>
            {qrCode && (
              <div style={{ display: 'flex', justifyContent: 'center', margin: '8px 0 12px' }}>
                {/* QR code 是 Supabase 產生的 data URL（SVG），只顯示這一次 */}
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={qrCode} alt="兩步驟驗證 QR code" width={180} height={180} style={{ backgroundColor: '#fff', padding: '8px', borderRadius: '8px' }} />
              </div>
            )}
            <details style={{ ...hint, marginBottom: '12px' }}>
              <summary style={{ cursor: 'pointer' }}>無法掃描？改用手動輸入金鑰</summary>
              <p style={{ ...hint, marginTop: '6px' }}>
                在 App 選「輸入設定金鑰」，帳戶名稱隨意，金鑰輸入：
                <code style={{ display: 'block', marginTop: '4px', padding: '6px 8px', borderRadius: '6px', backgroundColor: '#0f172a', color: '#e2e8f0', wordBreak: 'break-all' }}>{secret}</code>
              </p>
            </details>
          </>
        )}

        {mode === 'verify' && (
          <>
            <h1 style={{ fontSize: '18px', margin: '0 0 8px' }}>輸入驗證碼</h1>
            <p style={{ ...hint, marginBottom: '12px' }}>打開手機的驗證器 App，輸入目前顯示的 6 位數字（每 30 秒會換一次）。</p>
          </>
        )}

        {(mode === 'enroll' || mode === 'verify') && (
          <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            <input
              autoFocus
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              placeholder="6 位數驗證碼"
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
              style={{ padding: '10px 12px', fontSize: '20px', letterSpacing: '6px', textAlign: 'center', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.2)', backgroundColor: '#0f172a', color: '#f8fafc' }}
            />
            {errorMsg && <p style={{ ...hint, color: '#fca5a5' }}>{errorMsg}</p>}
            <button
              type="submit"
              disabled={busy || code.length !== 6}
              style={{ padding: '10px', borderRadius: '8px', border: 'none', backgroundColor: '#16a34a', color: '#fff', fontWeight: 'bold', fontSize: '14px', cursor: busy ? 'not-allowed' : 'pointer', opacity: busy || code.length !== 6 ? 0.5 : 1 }}
            >
              {busy ? '驗證中…' : mode === 'enroll' ? '完成設定' : '確認'}
            </button>
          </form>
        )}

        <p style={{ ...hint, marginTop: '16px', fontSize: '12px' }}>
          換手機或手機遺失，請聯絡平台管理者重設。
          <button onClick={handleSignOut} style={{ marginLeft: '8px', background: 'none', border: 'none', color: '#38bdf8', cursor: 'pointer', fontSize: '12px', padding: 0 }}>
            登出
          </button>
        </p>
      </div>
    </div>
  )
}
