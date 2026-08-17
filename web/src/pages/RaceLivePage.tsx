import { useParams } from "react-router-dom"

export function RaceLivePage() {
  const { sessionId } = useParams<{ sessionId: string }>()
  return <h1>Race {sessionId} — Live</h1>
}
