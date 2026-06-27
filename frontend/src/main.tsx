import React from 'react'
import ReactDOM from 'react-dom/client'

function App() {
  return (
    <div style={{padding: 40, fontFamily: 'system-ui'}}>
      <h1>Cante</h1>
      <p>Backoffice coming in M4-M5. API already live at <code>/v1/</code>.</p>
    </div>
  )
}

ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>)
