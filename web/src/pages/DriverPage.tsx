import { useParams } from "react-router-dom"

export function DriverPage() {
  const { driverId } = useParams<{ driverId: string }>()
  return <h1>Driver {driverId}</h1>
}
