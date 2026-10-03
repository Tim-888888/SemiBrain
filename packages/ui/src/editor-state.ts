import { ElMessageBox } from 'element-plus'
import 'element-plus/es/components/message-box/style/css'

const editors = new Set<() => 'busy' | 'dirty' | false>()
export function registerEditor(check: () => 'busy' | 'dirty' | false) {
  editors.add(check)
  return () => editors.delete(check)
}
export async function confirmDiscard() {
  try {
    await ElMessageBox.confirm('尚有未保存的修改，离开后这些修改将丢失。', '放弃本次修改？', {
      confirmButtonText: '放弃修改', cancelButtonText: '继续编辑', type: 'warning',
      distinguishCancelAndClose: true, closeOnClickModal: false,
    })
    return true
  } catch { return false }
}
export async function confirmNavigation() {
  const states = [...editors].map(check => check())
  if (states.includes('busy')) return false
  return !states.includes('dirty') || await confirmDiscard()
}
