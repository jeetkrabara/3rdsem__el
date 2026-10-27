import { Link, Route, Routes } from 'react-router-dom'
import Dashboard from './pages/Dashboard.jsx'
import LakePage from './pages/LakePage.jsx'
import AccuracyPage from './pages/AccuracyPage.jsx'
import ChatBot from './components/ChatBot.jsx'

export default function App() {
  return (
    <>
      <header className="topbar">
        <Link to="/" className="brand"><span className="brand-mark">≈</span> AquaRevive</Link>
        <span className="tagline">Explainable satellite monitoring of Bengaluru's lakes · Sentinel-2</span>
        <span className="spacer" />
        <nav className="row" style={{ gap: '1rem' }}>
          <Link to="/" className="navlink">Lakes</Link>
          <Link to="/accuracy" className="navlink">Accuracy</Link>
        </nav>
      </header>
      <main className="page">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/lake/:id" element={<LakePage />} />
          <Route path="/accuracy" element={<AccuracyPage />} />
        </Routes>
      </main>
      <ChatBot />
    </>
  )
}
