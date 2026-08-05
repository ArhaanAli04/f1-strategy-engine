import { useParams } from "react-router-dom"
import { CompetitorStrategyTable } from "@/components/strategy/CompetitorStrategyTable"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

export function RaceStrategyPage() {
  const { sessionId } = useParams<{ sessionId: string }>()

  if (!sessionId) {
    return <div className="p-6 text-sm text-muted-foreground">No session selected.</div>
  }

  return (
    <div className="p-6">
      <Card>
        <CardHeader>
          <CardTitle>Competitor Strategy</CardTitle>
        </CardHeader>
        <CardContent>
          <CompetitorStrategyTable sessionId={sessionId} />
        </CardContent>
      </Card>
    </div>
  )
}
