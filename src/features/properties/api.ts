import type { Apartment, ApartmentsResponse } from './types'

const backendBaseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

/** Local fallback when backend is offline — keeps map/cards working in dev. */
export const FALLBACK_APARTMENTS: Apartment[] = [
  {
    id: 'lodha-panache',
    name: 'Lodha Panache',
    price: '₹1.12 Cr Onwards',
    bhk: '2 & 3 BHK',
    address: 'Hinjewadi Phase 1, Pune',
    description: 'Premium gated community with clubhouse, swimming pool, and IT park proximity.',
    city: 'Pune',
    locality: 'Hinjewadi',
    latitude: 18.5912,
    longitude: 73.7389,
    tag: 'Premium',
  },
  {
    id: 'godrej-park',
    name: 'Godrej Park World',
    price: '₹78 L Onwards',
    bhk: '2 BHK',
    address: 'Hinjewadi Phase 2, Pune',
    description: 'Value-focused homes with landscaped gardens and strong rental demand near tech parks.',
    city: 'Pune',
    locality: 'Hinjewadi',
    latitude: 18.596,
    longitude: 73.731,
    tag: 'Best Value',
  },
  {
    id: 'vtp-skyii',
    name: 'VTP Skyii High',
    price: '₹1.85 Cr Onwards',
    bhk: '3 & 4 BHK',
    address: 'Kharadi, Pune',
    description: 'Luxury high-rise with panoramic views, smart home features, and premium amenities.',
    city: 'Pune',
    locality: 'Kharadi',
    latitude: 18.5534,
    longitude: 73.9497,
    tag: 'Luxury',
  },
  {
    id: 'lodha-amara',
    name: 'Lodha Amara',
    price: '₹2.4 Cr Onwards',
    bhk: '2 & 3 BHK',
    address: 'Thane West, Mumbai',
    description: 'Large township with schools, retail, and excellent connectivity to Mumbai.',
    city: 'Mumbai',
    locality: 'Thane',
    latitude: 19.2183,
    longitude: 72.9781,
    tag: 'Premium',
  },
  {
    id: 'prestige-lakeside',
    name: 'Prestige Lakeside Habitat',
    price: '₹1.65 Cr Onwards',
    bhk: '2 & 3 BHK',
    address: 'Powai, Mumbai',
    description: 'Lake-facing residences with top-tier amenities in Mumbai startup hub.',
    city: 'Mumbai',
    locality: 'Powai',
    latitude: 19.1176,
    longitude: 72.906,
    tag: 'Luxury',
  },
  {
    id: 'dlf-camellias',
    name: 'DLF Camellias',
    price: '₹8.5 Cr Onwards',
    bhk: '4 & 5 BHK',
    address: 'Golf Course Road, Gurugram',
    description: 'Ultra-luxury apartments with golf course views and concierge services.',
    city: 'Delhi',
    locality: 'Gurugram',
    latitude: 28.443,
    longitude: 77.1025,
    tag: 'Luxury',
  },
  {
    id: 'godrej-south',
    name: 'Godrej South Estate',
    price: '₹2.1 Cr Onwards',
    bhk: '3 & 4 BHK',
    address: 'Okhla, New Delhi',
    description: 'Modern towers with green spaces and metro connectivity in South Delhi.',
    city: 'Delhi',
    locality: 'Okhla',
    latitude: 28.5355,
    longitude: 77.291,
    tag: 'Premium',
  },
  {
    id: 'my-home-bhooja',
    name: 'My Home Bhooja',
    price: '₹95 L Onwards',
    bhk: '2 & 3 BHK',
    address: 'HITEC City, Hyderabad',
    description: 'Well-connected project near IT corridors with strong appreciation potential.',
    city: 'Hyderabad',
    locality: 'HITEC City',
    latitude: 17.4435,
    longitude: 78.3772,
    tag: 'Best Value',
  },
  {
    id: 'aparna-cyber',
    name: 'Aparna Cyber Commune',
    price: '₹1.35 Cr Onwards',
    bhk: '3 BHK',
    address: 'Gachibowli, Hyderabad',
    description: 'Premium community with sports facilities and close to financial district.',
    city: 'Hyderabad',
    locality: 'Gachibowli',
    latitude: 17.4401,
    longitude: 78.3489,
    tag: 'Premium',
  },
]

export async function fetchApartmentsByCity(city: string): Promise<Apartment[]> {
  try {
    const response = await fetch(
      `${backendBaseUrl}/properties/apartments?city=${encodeURIComponent(city)}`,
    )
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }
    const data: ApartmentsResponse = await response.json()
    return data.apartments
  } catch {
    if (!city.trim()) {
      return FALLBACK_APARTMENTS
    }

    return FALLBACK_APARTMENTS.filter((a) => a.city.toLowerCase() === city.toLowerCase())
  }
}

export async function fetchApartmentById(id: string): Promise<Apartment | null> {
  try {
    const response = await fetch(`${backendBaseUrl}/properties/apartments/${encodeURIComponent(id)}`)
    if (!response.ok) return null
    return (await response.json()) as Apartment
  } catch {
    return FALLBACK_APARTMENTS.find((a) => a.id === id) ?? null
  }
}
