// Each muscle group follows this shape:
// {
//   name: string,
//   training:   { sections: [{ title, items: string[] }] },
//   mobility:   { sections: [{ title, items: string[] }] },
//   stretching: { sections: [{ title, items: string[] }] },
//   recovery:   { sections: [{ title, items: string[] }] },
// }
//
// "sections" lets you group items under sub-headings within a tab.
// Use a single section with title: '' for a flat list.

const muscleData = {
  chest: {
    name: 'Chest',
    training:   { sections: [] },
    mobility:   { sections: [] },
    stretching: { sections: [] },
    recovery:   { sections: [] },
  },
  shoulders: {
    name: 'Shoulders',
    training:   { sections: [] },
    mobility:   { sections: [] },
    stretching: { sections: [] },
    recovery:   { sections: [] },
  },
  biceps: {
    name: 'Biceps',
    training:   { sections: [] },
    mobility:   { sections: [] },
    stretching: { sections: [] },
    recovery:   { sections: [] },
  },
  core: {
    name: 'Core',
    training:   { sections: [] },
    mobility:   { sections: [] },
    stretching: { sections: [] },
    recovery:   { sections: [] },
  },
  quads: {
    name: 'Quads',
    training:   { sections: [] },
    mobility:   { sections: [] },
    stretching: { sections: [] },
    recovery:   { sections: [] },
  },
  calves: {
    name: 'Calves',
    training:   { sections: [] },
    mobility:   { sections: [] },
    stretching: { sections: [] },
    recovery:   { sections: [] },
  },
  back: {
    name: 'Back',
    training:   { sections: [] },
    mobility:   { sections: [] },
    stretching: { sections: [] },
    recovery:   { sections: [] },
  },
  triceps: {
    name: 'Triceps',
    training:   { sections: [] },
    mobility:   { sections: [] },
    stretching: { sections: [] },
    recovery:   { sections: [] },
  },
  glutes: {
    name: 'Glutes',
    training:   { sections: [] },
    mobility:   { sections: [] },
    stretching: { sections: [] },
    recovery:   { sections: [] },
  },
  hamstrings: {
    name: 'Hamstrings',
    training:   { sections: [] },
    mobility:   { sections: [] },
    stretching: { sections: [] },
    recovery:   { sections: [] },
  },
};
