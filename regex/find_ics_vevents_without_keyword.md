## find ics vevents without keyword

The purpose: target calendar events that don't have the specified keyword in it. Replace `<keyword>` with the keyword that you don't want to be included in the targetted events.

```
(BEGIN:VEVENT)((.|\n)(?!(BEGIN:VEVENT))(?!(<keyword>)))*(END:VEVENT)
```