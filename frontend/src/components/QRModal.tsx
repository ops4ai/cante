import { useEffect, useState } from 'react'
import { useQuery } from 'react-query'
import { getQR } from '../api/numbers'
import { Modal, Button } from './Modal'

// Polls GET /v1/numbers/{id}/qr until the QR is shown or the instance reports
// connected, then offers the "Connect" action.
export function QRModal({ numberId, onClose, onConnected }: {
  numberId: string
  onClose: () => void
  onConnected?: () => void
}) {
  const [connected, setConnected] = useState(false)
  const { data, error, isLoading } = useQuery(
    ['qr', numberId],
    () => getQR(numberId),
    { refetchInterval: (data) => (data?.status && /open|qr|wait/i.test(data.status) ? 2000 : false), refetchIntervalInBackground: false },
  )

  useEffect(() => {
    if (data?.status && /conn|ready|ok/i.test(data.status)) setConnected(true)
  }, [data?.status])

  return (
    <Modal open onClose={onClose} title="Connect WhatsApp by QR">
      <div className="flex flex-col items-center gap-3">
        {isLoading && <div className="py-8 text-sm text-gray-500">Generating QR…</div>}
        {error && <div className="rounded bg-red-50 p-2 text-xs text-red-700">{String(error)}</div>}
        {data?.qr_code && (
          <div className="rounded-lg border border-gray-200 bg-white p-3">
            <img src={data.qr_code.startsWith('data:') ? data.qr_code : `data:image/png;base64,${data.qr_code}`} alt="QR" className="h-56 w-56" />
          </div>
        )}
        <div className="text-center text-xs text-gray-500">
          {connected
            ? '✓ Instance reports connected. You can close this.'
            : 'Open WhatsApp on your phone → Settings → Linked device → Scan this QR.'}
        </div>
        {data?.status && <div className="text-[10px] uppercase text-gray-400">status: {data.status}</div>}
        {connected ? (
          <Button onClick={() => { onConnected?.(); onClose() }}>Done</Button>
        ) : (
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
        )}
      </div>
    </Modal>
  )
}
