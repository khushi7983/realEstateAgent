import L from 'leaflet'
import type { Apartment } from '../properties/types'

export function buildPropertyMarkerHtml(apartment: Apartment, isActive: boolean): string {
  const tag = apartment.tag ?? 'Listed'
  return `
    <div class="property-map-marker ${isActive ? 'property-map-marker--active' : ''}" data-apartment-id="${apartment.id}">
      <div class="property-map-marker__card">
        <span class="property-map-marker__name">${apartment.name}</span>
        <span class="property-map-marker__tag">${tag}</span>
      </div>
      <div class="property-map-marker__stem"></div>
      <div class="property-map-marker__dot"></div>
    </div>
  `
}

export function createPropertyMarkerIcon(apartment: Apartment, isActive: boolean): L.DivIcon {
  return L.divIcon({
    className: 'property-marker-leaflet-anchor',
    html: buildPropertyMarkerHtml(apartment, isActive),
    iconSize: [180, 72],
    iconAnchor: [90, 72],
  })
}

export function fitMapToApartments(map: L.Map, apartments: Apartment[]) {
  if (apartments.length === 0) return

  if (apartments.length === 1) {
    const apt = apartments[0]
    map.flyTo([apt.latitude, apt.longitude], 14, { duration: 1.2 })
    return
  }

  const bounds = L.latLngBounds(
    apartments.map((apt) => [apt.latitude, apt.longitude] as [number, number]),
  )
  map.flyToBounds(bounds, { padding: [56, 56], duration: 1.2, maxZoom: 16 })
}
