"""X / Threads (§5.5): fase successiva, dietro `PROMO_X_ENABLED`.

Non e' ancora implementato: la pubblicazione di video su X richiede l'upload a pezzi con
OAuth utente, da fare solo sull'account del gioco. Finche' manca, un post per `x` finisce
`failed` con un motivo chiaro invece di restare in coda in silenzio; il file si carica a mano.
"""
from promo.publishers.base import Publisher, PublishResult


class XPublisher(Publisher):
    channel = "x"

    def describe(self, post, media_path):
        return "X non ancora implementato: il video va caricato a mano"

    def publish(self, post, media_path) -> PublishResult:
        return PublishResult.failure("publisher X non ancora implementato: caricare il video a mano")
