# Fixtures

**Uwaga: na razie SYNTETYCZNE.** Zbudowane ręcznie według schematów z OpenAPI PLK v1.1.4
(`ScheduleResponseDto`, `OperationResponseDto`, `CarriersResponse`), bo w środowisku,
w którym powstał kod, host `pdp-api.plk-sa.pl` był niedostępny.

ID stacji (30601 Poznań Gł., 80416 Kraków Gł.) są prawdziwe; pociągi, numery i ID tras — nie.

Do podmiany na prawdziwe zrzuty: `python -m app.capture` (patrz README w katalogu głównym),
potem dopasować oczekiwane wartości w testach.
