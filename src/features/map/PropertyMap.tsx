import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import type { Apartment } from '../properties/types'
import { CITY_CENTERS } from './cityCenters'
import { createPropertyMarkerIcon, fitMapToApartments } from './createPropertyMarker'
import './PropertyMap.css'

// @ts-ignore react-globe.gl types are not bundled
import Globe from 'react-globe.gl'

type PropertyMapProps = {
  city: string
  apartments: Apartment[]
  selectedApartment: Apartment | null
  zoomMode: 'globe' | 'city' | 'property'
  onApartmentSelect: (apartment: Apartment) => void
}

function LeafletMap({
  city,
  apartments,
  selectedApartment,
  zoomMode,
  onApartmentSelect,
}: PropertyMapProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<L.Map | null>(null)
  const markersRef = useRef<Map<string, L.Marker>>(new Map())
  const onSelectRef = useRef(onApartmentSelect)
  onSelectRef.current = onApartmentSelect

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return

    const center = CITY_CENTERS[city] ?? { lat: 20.5937, lng: 78.9629, zoom: 4 }
    const map = L.map(containerRef.current, {
      zoomControl: true,
      scrollWheelZoom: true,
    }).setView([center.lat, center.lng], center.zoom)

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      maxZoom: 19,
    }).addTo(map)

    mapRef.current = map

    return () => {
      map.remove()
      mapRef.current = null
      markersRef.current.clear()
    }
  }, [])

  const syncMarkers = (activeId: string | null, shouldFitBounds: boolean) => {
    const map = mapRef.current
    if (!map) return

    markersRef.current.forEach((marker) => marker.remove())
    markersRef.current.clear()

    apartments.forEach((apartment) => {
      const isActive = apartment.id === activeId
      const marker = L.marker([apartment.latitude, apartment.longitude], {
        icon: createPropertyMarkerIcon(apartment, isActive),
        zIndexOffset: isActive ? 1000 : 0,
      }).addTo(map)

      marker.on('click', () => onSelectRef.current(apartment))
      markersRef.current.set(apartment.id, marker)
    })

    requestAnimationFrame(() => {
      map.invalidateSize()
      if (shouldFitBounds && apartments.length > 0) {
        fitMapToApartments(map, apartments)
      }
    })
  }

  useEffect(() => {
    syncMarkers(selectedApartment?.id ?? null, zoomMode !== 'property')
  }, [city, apartments, zoomMode])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !selectedApartment || zoomMode !== 'property') return

    syncMarkers(selectedApartment.id, false)
    map.flyTo([selectedApartment.latitude, selectedApartment.longitude], 15, { duration: 1.5 })
  }, [selectedApartment, zoomMode])

  return (
    <div
      ref={containerRef}
      className="property-leaflet-map"
      aria-label={`Map of ${city}`}
      style={{ width: '100%', height: '100%' }}
    />
  )
}

function GlobeView() {
  const globeEl = useRef<any>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 })

  useEffect(() => {
    const updateSize = () => {
      if (containerRef.current) {
        setDimensions({
          width: containerRef.current.clientWidth,
          height: containerRef.current.clientHeight,
        })
      }
    }
    updateSize()
    window.addEventListener('resize', updateSize)
    return () => window.removeEventListener('resize', updateSize)
  }, [])

  useEffect(() => {
    if (globeEl.current) {
      globeEl.current.controls().autoRotate = true
      globeEl.current.controls().autoRotateSpeed = 0.8
      globeEl.current.pointOfView({ lat: 22, lng: 79, altitude: 1.5 }, 1000)
    }
  }, [dimensions.width])

  return (
    <div ref={containerRef} className="globe-view-container" style={{ width: '100%', height: '100%' }}>
      {dimensions.width > 0 && (
        <Globe
          ref={globeEl}
          width={dimensions.width}
          height={dimensions.height}
          globeImageUrl="//unpkg.com/three-globe/example/img/earth-blue-marble.jpg"
          backgroundColor="rgba(0,0,0,0)"
        />
      )}
    </div>
  )
}

export function PropertyMap(props: PropertyMapProps) {
  if (props.zoomMode === 'globe') {
    return <GlobeView />
  }
  return <LeafletMap {...props} />
}
