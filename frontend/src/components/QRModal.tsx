import { useEffect, useState } from 'react'
import { useQuery } from 'react-query'
import { getQR } from '../api/numbers'
import { Modal, Button } from './Modal'

export function QRModal({ numberId, onClose, onConnected }: {
  numberId: string
  onClose: () => void
  onConnected?: () => void
}) {
  const [done, setDone] = useState(false)
  const { data, error, isLoading } = useQuery(
    ['qr', numberId],
    () => getQR(numberId),
    { refetchInterval: (d) => (d?.status && d.status !== 'connected' ? 2000 : false), refetchIntervalInBackground: false },
  )

  useEffect(() => {
    if (data?.status === 'connected') {
      setDone(true)
      onConnected?.()
    }
  }, [data?.status, onConnected])

  const state = data?.status || 'loading'
  const hasQr = Boolean(data?.qr_code)

  const stateMessages: Record<string, string> = {
    close: 'Waiting for QR from WhatsApp…',
    connecting: 'Generating QR code…',
    open: 'QR scanned! Waiting for WhatsApp to confirm connection on your phone…',
    connected: '✓ WhatsApp connected successfully!',
    qr_pending: 'Requesting QR from WhatsApp…',
  }

  return (
    <Modal open onClose={onClose} title="Connect WhatsApp by QR">
      <div className="flex flex-col items-center gap-3">
        {isLoading && <div className="py-8 text-sm text-gray-500">Loading…</div>}
        {error && <div className="rounded bg-red-50 p-2 text-xs text-red-700">{String(error)}</div>}

        {hasQr && (
          <div className="rounded-lg border border-gray-200 bg-white p-3">
            <img src={data.qr_code.startsWith('data:') ? data.qr_code : `data:image/png;base64,${data.qr_code}`} alt="QR" className="h-56 w-56" />
          </div>
        )}

        <div className="text-center text-xs text-gray-500">
          {stateMessages[state] || `Status: ${state}`}
        </div>

        {state && <div className="text-[10px] uppercase text-gray-400">status: {state}</div>}

        {done ? (
          <Button onClick={() => { onClose() }}>Done</Button>
        ) : (
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
        )}
      </div>
    </Modal>
  )
}
