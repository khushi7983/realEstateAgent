import type { Apartment } from './types'

export const CITY_KEYWORDS: Record<string, string> = {
  pune: 'Pune',
  mumbai: 'Mumbai',
  delhi: 'Delhi',
  'new delhi': 'Delhi',
  hyderabad: 'Hyderabad',
  gurugram: 'Delhi',
  gurgaon: 'Delhi',
  ncr: 'Delhi',
  // ARK Group cities
  bengaluru: 'Bengaluru',
  bangalore: 'Bengaluru',
  whitefield: 'Bengaluru',
  kurnool: 'Kurnool',
  bhimavaram: 'Bhimavaram',
  suryapet: 'Suryapet',
  // Hyderabad sub-localities (ARK projects)
  bachupally: 'Hyderabad',
  uppal: 'Hyderabad',
  kondapur: 'Hyderabad',
  gachibowli: 'Hyderabad',
  'hitec city': 'Hyderabad',
  'hitec': 'Hyderabad',
  telangana: 'Hyderabad',
  'kongara kalan': 'Hyderabad',
  kharmanghat: 'Hyderabad',
}

export function detectCityFromText(text: string): string | null {
  const lower = text.toLowerCase()
  const sorted = Object.entries(CITY_KEYWORDS).sort((a, b) => b[0].length - a[0].length)
  for (const [keyword, city] of sorted) {
    if (lower.includes(keyword)) return city
  }
  return null
}

function apartmentMatchScore(text: string, apartment: Apartment): number {
  const lower = text.toLowerCase()
  const nameLower = apartment.name.toLowerCase()
  const localityLower = apartment.locality.toLowerCase()
  let score = 0

  if (lower.includes(nameLower)) score += 100

  const nameParts = nameLower.split(/[\s&]+/).filter((w) => w.length > 2)
  for (const part of nameParts) {
    if (lower.includes(part)) score += 20
  }

  if (lower.includes(localityLower)) score += 30

  const addressLower = apartment.address.toLowerCase()
  if (lower.includes(addressLower)) score += 40

  return score
}

export function matchApartmentFromText(text: string, apartments: Apartment[]): Apartment | null {
  if (!text.trim() || apartments.length === 0) return null

  const lower = text.toLowerCase()

  const ranked = apartments
    .map((apartment) => ({ apartment, score: apartmentMatchScore(text, apartment) }))
    .sort((a, b) => b.score - a.score)

  const top = ranked[0]
  if (!top || top.score < 20) {
    const showYouMatch = lower.match(/(?:show you|recommend|suggest|check out|look at|i like|pasand|choose)\s+(.{4,80})/i)
    if (showYouMatch) {
      const phrase = showYouMatch[1].split(/[.,!?]/)[0]
      const phraseRanked = apartments
        .map((apartment) => ({ apartment, score: apartmentMatchScore(phrase, apartment) }))
        .sort((a, b) => b.score - a.score)
      if (phraseRanked[0]?.score >= 20) return phraseRanked[0].apartment
    }
    return null
  }

  const second = ranked[1]
  const explicitChoice =
    /(?:show you|recommend|suggest|check out|look at|i like|pasand|choose|book|visit)/i.test(text)

  // Full name or explicit recommendation — always select
  if (top.score >= 100 || explicitChoice) return top.apartment

  // Multiple properties mentioned with similar scores → city overview only
  if (second && second.score >= 20 && top.score - second.score < 15) return null

  return top.score >= 40 ? top.apartment : null
}
