# Derive candidate DRC thresholds by measuring the published ASAP7 libraries.
#
# ASAP7's real DRM is not redistributable, so tech/drc/asap7_device.drc uses
# the tightest geometry the vendor actually draws as its threshold.  This
# script reports those measurements so the deck's numbers can be re-derived
# and audited rather than taken on faith.
#
# Zero-distance results are dropped: abutting and deliberately overlapping
# shapes (the select layers especially) report a zero gap that is not a rule.
#
# Run: klayout -b -r scripts/derive_asap7_drc_rules.rb -rd gds=<library>
#
# A derived value is only safe to put in the deck if the deck still runs clean
# on the vendor cells afterwards -- tests/run_8t_device_drc_check.sh re-checks
# that calibration on every run.

WIDTH_SPACE = [2, 7, 10, 11, 16, 17, 19, 20, 30, 40, 50, 60]
VIA_STACK = { 18 => [16, 19], 21 => [19, 20], 25 => [20, 30],
              35 => [30, 40], 45 => [40, 50], 55 => [50, 60] }
PROBE_NM = 60.0

layout = RBA::Layout.new
layout.read($gds)
dbu = layout.dbu
probe = (PROBE_NM / 1000.0 / dbu).round

ws = Hash.new { |h, k| h[k] = { w: nil, s: nil } }
enc = {}

region_for = lambda do |cell, lnum|
  li = layout.find_layer(RBA::LayerInfo.new(lnum, 0))
  next nil if li.nil?
  r = RBA::Region.new(cell.begin_shapes_rec(li))
  r.merge
  r.is_empty? ? nil : r
end
keep_min = lambda do |store, key, pairs|
  pairs.each do |p|
    d = p.distance
    next if d <= 0
    store[key] = d if store[key].nil? || d < store[key]
  end
end

layout.each_cell do |cell|
  WIDTH_SPACE.each do |lnum|
    r = region_for.call(cell, lnum)
    next if r.nil?
    keep_min.call(ws[lnum], :w, r.width_check(probe))
    keep_min.call(ws[lnum], :s, r.space_check(probe))
  end
  VIA_STACK.each do |via, metals|
    rv = region_for.call(cell, via)
    next if rv.nil?
    metals.each do |m|
      rm = region_for.call(cell, m)
      next if rm.nil?
      keep_min.call(enc, "V#{via}<M#{m}", rv.enclosed_check(rm, probe))
    end
  end
end

nm = lambda { |v| v ? format("%.3f", v * dbu * 1000) : "  >#{PROBE_NM.round}" }
puts "# measured from #{$gds} (probe window #{PROBE_NM.round} nm)"
puts "# layer   min-width(nm)   min-space(nm)"
WIDTH_SPACE.each do |l|
  next unless ws.key?(l)
  puts format("  %5d   %13s   %13s", l, nm.call(ws[l][:w]), nm.call(ws[l][:s]))
end
puts "# via enclosure (nm)"
enc.keys.sort.each { |k| puts format("  %-12s %s", k, nm.call(enc[k])) }
