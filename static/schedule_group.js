/* Assign complete groups to compatible staff and time intervals. */
const ScheduleGroup = (() => {
  const overlaps = (a,b) => a.staff.id===b.staff.id && a.start<b.end && b.start<a.end;

  function solve(options) {
    if (options.some(list=>!list.length)) return null;
    const assigned = Array(options.length).fill(null);
    // When everyone overlaps, use augmenting paths to avoid exponential searches.
    const fixed = options.every(list=>list.every(slot=>slot.start===list[0].start));
    if (fixed && Math.max(...options.map(list=>list[0].start)) < Math.min(...options.map(list=>list[0].end))) {
      const owners = new Map();
      const assign = (index,seen) => {
        for (const slot of options[index]) {
          const id = slot.staff.id;
          if (seen.has(id)) continue;
          seen.add(id);
          if (!owners.has(id) || assign(owners.get(id),seen)) {
            owners.set(id,index);
            assigned[index] = slot;
            return true;
          }
        }
        return false;
      };
      return options.every((_,index)=>assign(index,new Set())) ? assigned : null;
    }

    let remaining = 3000;
    function search(count) {
      if (count===options.length) return true;
      if (--remaining<0) return false;
      let index = -1;
      let candidates;
      for (let i=0;i<options.length;i++) {
        if (assigned[i]) continue;
        const available = options[i].filter(slot=>!assigned.some(other=>other && overlaps(slot,other)));
        if (!available.length) return false;
        if (!candidates || available.length<candidates.length) {
          index = i;
          candidates = available;
        }
      }
      for (const slot of candidates) {
        assigned[index] = slot;
        if (search(count+1)) return true;
        if (remaining<0) break;
      }
      assigned[index] = null;
      return false;
    }
    return search(0) ? assigned : null;
  }

  function roles(appointments, groups) {
    const byId = new Map(appointments.map(a=>[a.id,a]));
    const result = new Map();
    for (const group of groups) {
      if (group.automaticSplit) continue;
      const distinct = new Set(group.appointmentIds || []);
      const members = [...distinct].map(id=>byId.get(id)).filter(a=>a && !a.segmentOf);
      if (members.length<2) continue;
      const main = members[0].id;
      for (const member of members) result.set(member.id,{main,primary:member.id===main});
    }
    return result;
  }

  return {solve,roles};
})();
