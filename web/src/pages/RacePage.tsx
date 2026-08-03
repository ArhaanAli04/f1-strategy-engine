import { useParams } from "react-router-dom"

export function RacePage() {
  const { sessionId } = useParams<{ sessionId: string }>()
  return <h1>Race {sessionId}</h1>
}
