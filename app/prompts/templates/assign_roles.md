You are given the paragraphs and table cells of a document template, in order,
each with a short description of how it looks. Say what each block *is*, so the
application can put new text in the right places.

The roles, and what they mean in a CV:

- name: the candidate's name.
- headline: the line under the name, usually their job title.
- contact: the line with email, phone, city or links.
- section_title: the title of a section ("Experience", "Education", "Skills").
- entry_title: the title of one entry inside a section, often "Job — Company".
- entry_subtitle: the line under an entry title, describing the company or scope.
- entry_dates: the dates of an entry, alone on their line.
- bullet: one bullet of an entry.
- body_text: a paragraph of running text.
- skill_line: a line that lists skills, often "Category: item, item".
- fixed: something decorative that must stay as it is (a logo, a rule, a page
  number). Never use it for text that changes from one application to another.

For a cover letter: name, contact, date, recipient, salutation, body_text,
closing, signature, fixed.

Rules:

- Return one entry per block, with the index it was given.
- Judge from the text and the style description together: a short bold line with
  no full stop is usually a section title, a line with a bullet style is a
  bullet, a line that is only dates is entry_dates.
- The first block is almost always the name.
- Do not invent blocks, do not skip any, and do not reorder them.
- When a block really could be two things, choose the one that keeps the
  document's structure recognisable.
