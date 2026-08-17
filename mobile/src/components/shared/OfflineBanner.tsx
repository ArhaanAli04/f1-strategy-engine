import { useNetInfo } from "@react-native-community/netinfo"
import { Text, View } from "react-native"

interface OfflineBannerProps {
  // Pass the most relevant screen query's `dataUpdatedAt` (react-query
  // returns this on every useQuery result) so the banner can show when the
  // currently-displayed data was last actually fetched — react-query keeps
  // serving that last-successful value from its in-memory cache while
  // offline, it just stops being able to refresh it.
  dataUpdatedAt?: number | null
}

function formatStaleTime(ms: number): string {
  return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
}

// RN equivalent of web/src/components/shared/HistoricalDataBanner.tsx's
// informational blue tone — same styling family, different trigger
// (network connectivity via @react-native-community/netinfo's useNetInfo,
// not "no live session"). isConnected === false is the only state this
// renders for; null (still determining, most platforms' default on first
// render) and true both render nothing, so the banner doesn't flash on
// every screen mount before NetInfo's first event arrives.
export function OfflineBanner({ dataUpdatedAt }: OfflineBannerProps) {
  const netInfo = useNetInfo()
  if (netInfo.isConnected !== false) return null

  return (
    <View className="flex-row items-center justify-between gap-3 border-b border-blue-900/40 bg-blue-950/40 px-4 py-2">
      <Text className="flex-1 text-xs text-blue-200">
        You're offline — showing cached data
        {dataUpdatedAt ? ` from ${formatStaleTime(dataUpdatedAt)}` : ""}
      </Text>
    </View>
  )
}
