import { useParams } from "react-router-dom"

export function RaceStrategyPage() {
  const { sessionId } = useParams<{ sessionId: string }>()
  return <h1>Race {sessionId} — Strategy</h1>
}
