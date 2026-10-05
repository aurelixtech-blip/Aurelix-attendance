import { useEffect, useState } from 'react'
import api from './services/api'
import EmployeeAttendanceUndo from './EmployeeAttendanceUndo'
import { formatIndiaDate, formatKolkataTime } from './time'

function formatLocation(location) {
  if (!location || location.latitude == null || location.longitude == null) return 'Unavailable'
  if (location.display_name) return location.display_name
  if (location.area) return location.city && !location.area.includes(location.city) ? `${location.area}, ${location.city}` : location.area
  return 'Location unavailable'
}

function statusBadgeClass(status) {
  return status === 'ABSENT' ? 'badge absent' : 'badge verified'
}

export default function EmployeeAttendanceHistory() {
  const [items, setItems] = useState([])
  async function load() { const { data } = await api.get('/api/attendance/mine'); setItems(data) }
  useEffect(() => { load().catch(() => {}) }, [])
  return <section className="panel table-panel"><span className="eyebrow">MY ATTENDANCE</span><h2>Attendance history</h2><div className="table-scroll"><table><thead><tr><th>Date</th><th>Check in</th><th>Check-in location</th><th>Check out</th><th>Check-out location</th><th>Status</th><th>Undo Events</th></tr></thead><tbody>{items.map(item => <tr key={item.attendance_id}><td><b>{formatIndiaDate(item.date)}</b></td><td>{item.check_in_time ? `${formatKolkataTime(item.check_in_time)} IST` : '-'}</td><td>{formatLocation(item.check_in_location)}</td><td>{item.check_out_time ? `${formatKolkataTime(item.check_out_time)} IST` : 'Open'}</td><td>{formatLocation(item.check_out_location)}</td><td><span className={statusBadgeClass(item.final_status)}>{item.final_status}</span></td><td><EmployeeAttendanceUndo record={item} onRefresh={setItems}/></td></tr>)}</tbody></table></div></section>
}
