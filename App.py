import json
from dataclasses import dataclass, asdict, field
from datetime import date, datetime
from typing import List, Optional, Dict, Any, Tuple
from dateutil.relativedelta import relativedelta
from io import BytesIO
import re

import pandas as pd
import streamlit as st

# =========================
# Datenmodelle
# =========================

STOP_LEVELS = [
    "S (Substitution/Quelle entfernen)",
    "T (Technisch)",
    "O (Organisatorisch)",
    "P (PSA)",
    "Q (Qualifikation/Unterweisung)"
]
STATUS_LIST = ["offen", "in Umsetzung", "wirksam", "nicht wirksam", "entfallen"]

@dataclass
class Measure:
    title: str
    stop_level: str
    responsible: str = ""
    due_date: Optional[str] = None  # ISO
    status: str = "offen"
    notes: str = ""

@dataclass
class Hazard:
    id: str
    area: str
    activity: str
    hazard: str
    sources: List[str]
    existing_controls: List[str]
    prob: int = 3
    sev: int = 3
    risk_value: int = 9
    risk_level: str = "mittel"
    additional_measures: List[Measure] = field(default_factory=list)
    last_review: Optional[str] = None
    reviewer: str = ""
    documentation_note: str = ""

@dataclass
class Assessment:
    company: str
    location: str
    created_at: str
    created_by: str
    industry: str = "Hotel/Gastgewerbe"
    scope_note: str = ""
    risk_matrix_thresholds: Dict[str, List[int]] = field(default_factory=lambda: {"thresholds": [6, 12, 16]})
    hazards: List[Hazard] = field(default_factory=list)
    measures_plan_note: str = ""
    documentation_note: str = ""
    next_review_hint: str = ""

# =========================
# Utility
# =========================

def compute_risk(prob: int, sev: int, thresholds: List[int]) -> Tuple[int, str]:
    v = prob * sev
    if v <= thresholds[0]:
        return v, "niedrig"
    elif v <= thresholds[1]:
        return v, "mittel"
    elif v <= thresholds[2]:
        return v, "hoch"
    else:
        return v, "sehr hoch"

def hazard_to_row(h: Hazard) -> Dict[str, Any]:
    return {
        "ID": h.id, "Bereich": h.area, "Tätigkeit": h.activity, "Gefährdung": h.hazard,
        "Quellen/Einwirkungen": "; ".join(h.sources), "Bestehende Maßnahmen": "; ".join(h.existing_controls),
        "Eintrittswahrscheinlichkeit (1-5)": h.prob, "Schadensschwere (1-5)": h.sev,
        "Risikosumme": h.risk_value, "Risikostufe": h.risk_level,
        "Letzte Prüfung": h.last_review or "", "Prüfer/in": h.reviewer,
        "Dokumentationshinweis": h.documentation_note
    }

def measures_to_rows(h: Hazard) -> List[Dict[str, Any]]:
    rows = []
    for m in h.additional_measures:
        rows.append({
            "Gefährdungs-ID": h.id, "Bereich": h.area, "Gefährdung": h.hazard,
            "Maßnahme": m.title, "STOP(+Q)": m.stop_level, "Verantwortlich": m.responsible,
            "Fällig am": m.due_date or "", "Status": m.status, "Hinweis": m.notes
        })
    return rows

def new_id(prefix="HZ", n=4) -> str:
    ts = datetime.now().strftime("%y%m%d%H%M%S%f")[-n:]
    return f"{prefix}-{int(datetime.now().timestamp())}-{ts}"

def dump_excel(assess: Assessment) -> bytes:
    hazards_df = pd.DataFrame([hazard_to_row(h) for h in assess.hazards])
    measures_df = pd.DataFrame([r for h in assess.hazards for r in measures_to_rows(h)])
    meta = {
        "Unternehmen": assess.company, "Standort": assess.location,
        "Erstellt am": assess.created_at, "Erstellt von": assess.created_by,
        "Branche": assess.industry, "Umfang/Scope": assess.scope_note,
        "Maßnahmenplan-Hinweis": assess.measures_plan_note,
        "Dokumentationshinweis": assess.documentation_note,
        "Fortschreibung/Nächster Anlass": assess.next_review_hint
    }
    meta_df = pd.DataFrame(list(meta.items()), columns=["Feld", "Wert"])
    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        meta_df.to_excel(writer, sheet_name="00_Meta", index=False)
        hazards_df.to_excel(writer, sheet_name="10_Gefaehrdungen", index=False)
        measures_df.to_excel(writer, sheet_name="20_Massnahmen", index=False)
        thresholds = assess.risk_matrix_thresholds["thresholds"]
        conf_df = pd.DataFrame(
            {"Grenzen (Risikosumme)": ["niedrig ≤", "mittel ≤", "hoch ≤", "sehr hoch >"],
             "Wert": [thresholds[0], thresholds[1], thresholds[2], thresholds[2]]}
        )
        conf_df.to_excel(writer, sheet_name="90_Konfiguration", index=False)
    bio.seek(0)
    return bio.read()

def as_json(assess: Assessment) -> str:
    return json.dumps(asdict(assess), ensure_ascii=False, indent=2)

def from_json(s: str) -> Assessment:
    data = json.loads(s)
    hazards = []
    for h in data.get("hazards", []):
        measures = [Measure(**m) for m in h.get("additional_measures", [])]
        hazards.append(Hazard(
            id=h["id"], area=h["area"], activity=h["activity"], hazard=h["hazard"],
            sources=h.get("sources", []),
            existing_controls=h.get("existing_controls", h.get("existing", [])),  # rückwärtskompatibel
            prob=h.get("prob", 3), sev=h.get("sev", 3),
            risk_value=h.get("risk_value", 9), risk_level=h.get("risk_level", "mittel"),
            additional_measures=measures, last_review=h.get("last_review"),
            reviewer=h.get("reviewer", ""), documentation_note=h.get("documentation_note", "")
        ))
    return Assessment(
        company=data.get("company",""), location=data.get("location",""),
        created_at=data.get("created_at",""), created_by=data.get("created_by",""),
        industry=data.get("industry","Hotel/Gastgewerbe"), scope_note=data.get("scope_note", ""),
        risk_matrix_thresholds=data.get("risk_matrix_thresholds", {"thresholds":[6,12,16]}),
        hazards=hazards, measures_plan_note=data.get("measures_plan_note",""),
        documentation_note=data.get("documentation_note",""), next_review_hint=data.get("next_review_hint","")
    )

def slug(*parts: str) -> str:
    s = "_".join(parts)
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", s)
    return s[:80]

# =========================
# Branchen-Bibliothek (stark erweitert)
# =========================

def M(title, stop="O (Organisatorisch)"):
    return {"title": title, "stop_level": stop}

# ---------------------------
# HOTEL / GASTGEWERBE
# ---------------------------
LIB_HOTEL = {
    "Küche": [
        {"activity":"Kochen (Töpfe/Kessel)","hazard":"Hitze, heiße Flüssigkeiten, Verbrühungen/Verbrennungen","sources":["Herde","Kessel","Töpfe"],"existing":["Hitzeschutz"],"measures":[M("Topfdeckel/Spritzschutz","T (Technisch)"),M("‚Heiß!‘ rufen"),M("Hitzeschutzhandschuhe","P (PSA)")]},
        {"activity":"Braten (Pfanne/Grillplatte)","hazard":"Fettspritzer, Verbrennungen, Rauch/Dämpfe","sources":["Pfannen","Grillplatten"],"existing":["Abzug"],"measures":[M("Spritzschutz","T (Technisch)"),M("Haubenreinigung/Filterplan")]},
        {"activity":"Frittieren","hazard":"Fettbrand, Verbrennungen, Spritzer","sources":["Fritteusen"],"existing":["Fettbrandlöscher"],"measures":[M("Ölwechsel-/Reinigungsplan"),M("Hitzeschutzschürze & Handschuhe","P (PSA)"),M("Kein Wasser!")]},
        {"activity":"Kombidämpfer öffnen","hazard":"Heißdampf/Heißluft – Verbrühung beim Öffnen","sources":["Kombidämpfer"],"existing":["Abkühlzeit"],"measures":[M("Tür erst spaltweise öffnen"),M("Schutzhandschuhe","P (PSA)")]},
        {"activity":"Sous-vide / Wasserbad","hazard":"Heißwasser/Verbrühung, Strom (Tischgeräte)","sources":["Wasserbad","Beutel"],"existing":["Hitzeschutz"],"measures":[M("Max. Füllhöhe markieren","T (Technisch)"),M("Gerätekabel sichern")]},
        {"activity":"Salamander/Gratinieren","hazard":"Strahlungswärme/Verbrennung","sources":["Salamander"],"existing":["Hitzeschutz"],"measures":[M("Griff-/Abstellflächen frei halten")]},
        {"activity":"Mikrowelle/Regenerieren","hazard":"Wärme/Kontaktverbrennung, falsche Gefäße","sources":["Mikrowelle"],"existing":["Hinweisschilder"],"measures":[M("Nur geeignete Behälter verwenden")]},
        {"activity":"Mixer/Pürierstab","hazard":"Schnitt/Schlag, Strom, Spritzer","sources":["Pürierstab","Standmixer"],"existing":["Sichtprüfung","Schutz"],"measures":[M("Nur stromlos wechseln/reinigen")]},
        {"activity":"Schneiden mit Messern","hazard":"Schnitt-/Stichverletzungen","sources":["Messer"],"existing":["Scharfe Messer"],"measures":[M("Schleifplan"),M("Schnittschutzhandschuh bei Bedarf","P (PSA)")]},
        {"activity":"Aufschnittmaschine","hazard":"Schnitt an rotierenden Klingen","sources":["Aufschnitt"],"existing":["Schutzhaube","Not-Aus"],"measures":[M("Interlocks prüfen","T (Technisch)"),M("Nur Befugte")]},
        {"activity":"Fleischwolf/Gemüseschneider","hazard":"Eingezogenwerden, Schnittverletzung","sources":["Wolf","Gemüseschneider"],"existing":["Stopfer"],"measures":[M("Stopfer verwenden"),M("Not-Aus/Einzug Unterweisung","Q (Qualifikation/Unterweisung)")]},
        {"activity":"Kippkessel/Bräter","hazard":"Verbrühung, Quetschen beim Kippen","sources":["Kippkessel"],"existing":["Hitzeschutz"],"measures":[M("Kipp-Prozess standardisieren"),M("Zweihandbedienung beachten","Q (Qualifikation/Unterweisung)")]},
        {"activity":"Spülbereich/Stewarding","hazard":"Heißes Wasser/Dampf, Chemikalien, Rutsch","sources":["Spülmaschine","Klarspüler"],"existing":["Hand-/Augenschutz"],"measures":[M("Sofort-Wisch-Regel"),M("Antirutsch-Matten","T (Technisch)")]},
        {"activity":"Reinigung/Chemie","hazard":"Ätz-/Reizwirkung, Chlorgas bei Mischungen","sources":["Reiniger/Desinfektion"],"existing":["Dosiersysteme"],"measures":[M("Vordosierte Kartuschen","S (Substitution/Quelle entfernen)"),M("Betriebsanweisungen aushängen")]},
        {"activity":"Gasgeräte","hazard":"Gasleck, CO-Bildung, Brand/Explosion","sources":["Gasherde","Leitungen"],"existing":["Dichtheitsprüfung"],"measures":[M("Gaswarnmelder","T (Technisch)"),M("Leckcheck vor Start")]},
        {"activity":"Warenannahme/Hubwagen","hazard":"Quetschungen, Heben/Tragen, Verkehrswege","sources":["Rollcontainer","Hubwagen"],"existing":["Hebehilfen"],"measures":[M("Wege kennzeichnen"),M("Kurzunterweisung Heben/Tragen","Q (Qualifikation/Unterweisung)")]},
        {"activity":"Altöl/Müll entsorgen","hazard":"Verbrennung bei heißem Öl, Schnitt/Infektion","sources":["Altöl","Müllsack"],"existing":["Abkühlen"],"measures":[M("Deckel-Transportbehälter","T (Technisch)"),M("Handschutz verpflichtend","P (PSA)")]},
        {"activity":"TK-/Kühlräume","hazard":"Kälte, Rutschgefahr, Einsperr-Risiko","sources":["Kühlzelle","TK"],"existing":["Kälteschutz"],"measures":[M("Tür-Notöffnung prüfen","T (Technisch)"),M("Aufenthaltsdauer begrenzen")]},
        {"activity":"Allergenmanagement","hazard":"Kreuzkontamination/Allergene","sources":["Zutatenwechsel"],"existing":["Kennzeichnung"],"measures":[M("Rein-/Unrein-Organisation"),M("Unterweisung LMIV","Q (Qualifikation/Unterweisung)")]},
        {"activity":"Elektrische Kleingeräte","hazard":"Stromschlag, Brand","sources":["Mixer","Pürierstab"],"existing":["Sichtprüfung"],"measures":[M("Prüfintervall ortsveränderliche Geräte")]},
        {"activity":"Heißwasserbereiter/Boiler","hazard":"Verbrühung/Dampf","sources":["Boiler"],"existing":["Hinweise"],"measures":[M("Auslauftemperatur begrenzen","T (Technisch)")]},
        {"activity":"Vakuumieren/Schweißen","hazard":"Quetschung/Verbrennung","sources":["Vakuumierer"],"existing":["Hitzeschutz"],"measures":[M("Heißzonen kennzeichnen","T (Technisch)")]},
    ],
    "Housekeeping": [
        {"activity":"Betten machen","hazard":"Rücken-/Schulterbelastung","sources":["Matratzen"],"existing":["Arbeitstechnik"],"measures":[M("Ecken-Technik schulen","Q (Qualifikation/Unterweisung)"),M("Leichtere Bettwaren","S (Substitution/Quelle entfernen)")]},
        {"activity":"Sanitärreinigung","hazard":"Chemikalien/Aerosole","sources":["Reiniger"],"existing":["Hautschutzplan"],"measures":[M("Dosierstation/Piktogramme","T (Technisch)"),M("Sprühnebel vermeiden","S (Substitution/Quelle entfernen)")]},
        {"activity":"Fenster/Glas innen","hazard":"Sturz, Schnitt an Glas","sources":["Leitern","Glas"],"existing":["Leiterprüfung"],"measures":[M("Teleskopstiele statt Leiter","S (Substitution/Quelle entfernen)"),M("Schnittfeste Handschuhe","P (PSA)")]},
        {"activity":"Wäschetransport","hazard":"Heben/Tragen, Quetschungen","sources":["Wäschewagen"],"existing":["Schiebehilfen"],"measures":[M("Lastbegrenzung"),M("Türen offen sichern")]},
        {"activity":"Abfallentsorgung","hazard":"Stich-/Schnitt, Infektion","sources":["Scherben","Nadeln"],"existing":["Feste Behälter"],"measures":[M("Sharps-Boxen","T (Technisch)"),M("Meldeweg Nadel-/Scherbenfund")]},
    ],
    "Service/Bar": [
        {"activity":"Heißgetränke zubereiten","hazard":"Verbrühungen/Verbrennungen","sources":["Kaffeemaschine"],"existing":["Hitzeschutz"],"measures":[M("Dampflanze abblasen"),M("Handschutz bereit","P (PSA)")]},
        {"activity":"Flambieren/Offene Flamme","hazard":"Brand/Alkoholdämpfe","sources":["Brenner","Spirituosen"],"existing":["Abstand"],"measures":[M("Nur geschultes Personal"),M("Löschmittel bereit")]},
        {"activity":"CO₂-Zapfanlage/Flaschenwechsel","hazard":"Erstickung/Hochdruck","sources":["CO₂-Flaschen"],"existing":["CO₂-Warner"],"measures":[M("Sensorentest dokumentieren","T (Technisch)"),M("Wechsel nur zu zweit")]},
        {"activity":"Gläser polieren/Bruch","hazard":"Schnittverletzungen","sources":["Glas"],"existing":["Entsorgung"],"measures":[M("Polierhandschuhe","P (PSA)")]},
        {"activity":"Eiscrusher/Mixer","hazard":"Schnitt/Strom","sources":["Crusher","Mixer"],"existing":["Schutzhauben"],"measures":[M("Nur stromlos reinigen")]},
    ],
    "Technik/Haustechnik": [
        {"activity":"Elektroarbeiten (EUP/EFK)","hazard":"Elektrischer Schlag, Lichtbogen","sources":["Verteilungen"],"existing":["LOTO"],"measures":[M("LOTO-Verfahren dokumentieren"),M("PSA+Prüfer anwenden","T (Technisch)")]},
        {"activity":"Heißarbeiten (Schweißen/Trennen)","hazard":"Brand/Explosion, Rauch","sources":["Schweißgerät"],"existing":["Genehmigung","Feuerwache"],"measures":[M("Funkenschutz","T (Technisch)"),M("Nachkontrolle")]},
        {"activity":"Dach-/Höhenarbeit","hazard":"Absturz","sources":["Dachkanten"],"existing":["PSAgA"],"measures":[M("Anschlagpunkte prüfen","T (Technisch)"),M("Rettungsplan")]},
        {"activity":"Legionellen/Trinkwasser","hazard":"Biologische Risiken","sources":["Warmwassersysteme"],"existing":["Temperaturplan"],"measures":[M("Thermische Desinfektion/Probenplan")]},
    ],
    "Lager/Wareneingang": [
        {"activity":"Auspacken/Öffnen","hazard":"Schnittverletzungen, Stolpern","sources":["Cutter","Umreifungen"],"existing":["Sichere Messer"],"measures":[M("Sicherheitsmesser einsetzen","S (Substitution/Quelle entfernen)"),M("Müll-Station nahe Rampe")]},
        {"activity":"Palettieren/Bewegen","hazard":"Quetschungen, Anfahren","sources":["Rollcontainer","Hubwagen"],"existing":["Wege markieren"],"measures":[M("Stopper an Rampen","T (Technisch)"),M("Verkehrsordnung aushängen")]},
        {"activity":"Hochregal/Entnahme in Höhe","hazard":"Absturz/Herabfallende Teile","sources":["Leitern","Regale"],"existing":["Leiterprüfung"],"measures":[M("Nur geprüfte Tritte"),M("Lastsicherung kontrollieren")]},
        {"activity":"TK-Lager/Kälte","hazard":"Kälte, Rutsch","sources":["Eis","Kondenswasser"],"existing":["Kälteschutz"],"measures":[M("Aufenthaltsdauer begrenzen"),M("Eis entfernen/Matten","T (Technisch)")]},
        {"activity":"Leergut/Altglas","hazard":"Schnitt/Quetschung, Lärm","sources":["Kisten","Flaschen"],"existing":["Handschutz","Gehörschutz"],"measures":[M("Scherben sofort beseitigen")]},
    ],
    "Spa/Wellness": [
        {"activity":"Sauna/Ofen & Aufguss","hazard":"Verbrennungen, Brand, Heißdampf","sources":["Saunaöfen"],"existing":["Abschirmungen"],"measures":[M("Ofenschutz/Temperaturwächter prüfen","T (Technisch)"),M("Aufgussregeln festlegen")]},
        {"activity":"Pooltechnik/Chemie","hazard":"Gefahrstoffe (Chlor, pH), Gasfreisetzung","sources":["Dosier-/Lagerräume"],"existing":["Lüftung/Absaugung"],"measures":[M("Auffangwannen/Trennung","T (Technisch)"),M("Freigabe mit Gaswarner")]},
        {"activity":"Nassbereiche","hazard":"Rutsch-/Sturzgefahr","sources":["Fliesen","Wasser"],"existing":["Rutschhemmung"],"measures":[M("Rutschmatten/Beläge prüfen","T (Technisch)"),M("Sofort-Wisch-Regel & Sperrung")]},
        {"activity":"Therapien/Massage","hazard":"Ergonomie/Infektionen","sources":["Öle","Kontakt"],"existing":["Hygieneplan"],"measures":[M("Höhenverstellbare Liegen","T (Technisch)")]},
    ],
    "Rezeption": [
        {"activity":"Front Office/Gästekommunikation","hazard":"Psychische Belastung, Konflikte","sources":["Stoßzeiten"],"existing":["Deeskalation"],"measures":[M("Stoßzeiten doppelt besetzen")]},
        {"activity":"Nacht-/Alleinarbeit","hazard":"Überfall/Bedrohung, Ermüdung","sources":["Nachtschicht"],"existing":["Alarmtaster"],"measures":[M("Stillen Alarm testen","T (Technisch)"),M("Zwei-Personen-Regel nach Risiko")]},
        {"activity":"Bildschirm/Kasse","hazard":"Ergonomie, Augenbelastung","sources":["Monitore"],"existing":["Ergonomiecheck"],"measures":[M("20-20-20-Regel & Mikropausen"),M("Sehtest/Bildschirmbrille","Q (Qualifikation/Unterweisung)")]},
    ],
    "Verwaltung": [
        {"activity":"Bildschirmarbeit","hazard":"Haltungs-/Augenbelastung","sources":["Sitzplätze","Monitore"],"existing":["Höhenverstellbar"],"measures":[M("Monitorhöhe/Abstand einstellen","T (Technisch)"),M("Mikropausenregelung")]},
        {"activity":"Laserdrucker/Toner","hazard":"Feinstaub, Hautkontakt","sources":["Tonerwechsel"],"existing":["Lüftung"],"measures":[M("Wechselhandschuhe/Abfallbeutel","T (Technisch)")]},
    ],
    "Außenbereiche": [
        {"activity":"Gartenpflege/Mähen","hazard":"Projektilwurf, Lärm","sources":["Rasenmäher"],"existing":["Schutzbrille","Gehörschutz"],"measures":[M("Steinkontrolle vor Start"),M("Visier/Gehörschutz","P (PSA)")]},
        {"activity":"Hecken-/Baumschnitt","hazard":"Schnittverletzung, Absturz","sources":["Heckenschere","Leiter"],"existing":["Leiter sichern"],"measures":[M("Teleskopgeräte statt Leiter","S (Substitution/Quelle entfernen)")]},
        {"activity":"Winterdienst","hazard":"Rutschen, Kälte","sources":["Eis/Schnee"],"existing":["Räum-/Streuplan"],"measures":[M("Rutschhemmende Spikes/Schuhe","P (PSA)"),M("Prioritätswege & Frühstartplan")]},
    ],
}

# ---------------------------
# BÄCKEREI
# ---------------------------
LIB_BAECKEREI = {
    "Produktion": [
        {"activity":"Backen am Etagen-/Stikkenofen","hazard":"Hitze/Verbrennung, Dampf","sources":["Öfen","Backwagen"],"existing":["Hitzeschutz"],"measures":[M("Backwagen fixieren"),M("Hitzeschutzhandschuhe","P (PSA)")]},
        {"activity":"Knetmaschine/Spiral-/Hubkneter","hazard":"Eingezogenwerden/Quetschen","sources":["Knetmaschine"],"existing":["Schutzhaube","Not-Aus"],"measures":[M("Hauben-/Not-Aus-Prüfplan","T (Technisch)")]},
        {"activity":"Teigteiler/Rundwirker","hazard":"Quetschen/Schnitt","sources":["Teigteiler","Rundwirker"],"existing":["Schutzvorrichtungen"],"measures":[M("Reinigung nur stromlos")]},
        {"activity":"Ausziehen/Ofenschießen","hazard":"Verbrennung/Überlastung","sources":["Schießer","Bleche"],"existing":["Ofenhandschuhe"],"measures":[M("Zweitperson bei schweren Wagen")]},
        {"activity":"Fritteuse/Schmalzgebäck","hazard":"Fettbrand/Verbrennung","sources":["Fritteuse"],"existing":["Fettbrandlöscher"],"measures":[M("Öltemperatur/Wechselplan")]},
        {"activity":"Mehlstaub/Abwiegen","hazard":"Staubexposition/Explosion","sources":["Mehlstaub"],"existing":["Absaugung"],"measures":[M("Staubarme Dosierung","S (Substitution/Quelle entfernen)")]},
        {"activity":"Schockfrosten/Kühlräume","hazard":"Kälte/Rutsch","sources":["TK","Kühlräume"],"existing":["Kälteschutz"],"measures":[M("Aufenthaltsdauer begrenzen")]},
        {"activity":"Reinigung/Desinfektion","hazard":"Chemikalien/Ätzwirkung","sources":["Reiniger"],"existing":["Haut-/Augenschutz"],"measures":[M("Dosierstationen & BA","T (Technisch)")]},
        {"activity":"Dekor/Zuckerguss","hazard":"Ergonomie, Rutsch","sources":["Zucker","Fette"],"existing":["Rutschschutz"],"measures":[M("Antirutschmatten","T (Technisch)")]},
    ],
    "Verkauf": [
        {"activity":"Brotschneidemaschine","hazard":"Schnittverletzung","sources":["Brotschneider"],"existing":["Schutzhaube"],"measures":[M("Nur befugte Bedienung")]},
        {"activity":"Heißgetränke","hazard":"Verbrühung","sources":["Kaffeemaschine"],"existing":["Hitzeschutz"],"measures":[M("Dampflanze abblasen")]},
        {"activity":"Kasse/Überfallrisiko","hazard":"Konflikt/Überfall (betriebsabhängig)","sources":["Kasse"],"existing":["Schulung"],"measures":[M("Deeskalation/Regelwerk")]},
        {"activity":"Allergenkennzeichnung","hazard":"Fehlkennzeichnung","sources":["Backwaren"],"existing":["Kennzeichnung"],"measures":[M("Vier-Augen-Prinzip Etiketten")]},
        {"activity":"Vitrine/Glasbruch","hazard":"Schnitt/Verunreinigung","sources":["Vitrine"],"existing":["Reinigung"],"measures":[M("Glasbruch-Notfallset")]},
    ],
    "Logistik": [
        {"activity":"Lieferung/Backwagen","hazard":"Quetschungen/Sturz","sources":["Backwagen","Rampe"],"existing":["Stopper"],"measures":[M("Rampe sichern","T (Technisch)")]},
        {"activity":"Palettieren/Transport","hazard":"Anfahren/Quetschen","sources":["Paletten","Hubwagen"],"existing":["Wegeordnung"],"measures":[M("Vorfahrt/Signale aushängen")]},
    ]
}

# ---------------------------
# FLEISCHEREI / METZGEREI
# ---------------------------
LIB_FLEISCHEREI = {
    "Produktion": [
        {"activity":"Bandsäge","hazard":"Schnitt/Amputation","sources":["Bandsäge"],"existing":["Schutzhaube","Not-Aus"],"measures":[M("Nur befugte Bedienung"),M("Reinigung stromlos")]},
        {"activity":"Fleischwolf","hazard":"Eingezogenwerden","sources":["Fleischwolf"],"existing":["Stopfer","Schutz"],"measures":[M("Stopfer konsequent nutzen")]},
        {"activity":"Kutter","hazard":"Schnitt/Schlag","sources":["Kutter"],"existing":["Haube","Verriegelung"],"measures":[M("Verriegelung prüfen","T (Technisch)")]},
        {"activity":"Vakuumierer/Schrumpfer","hazard":"Verbrennung/Quetschung","sources":["Heißsiegel"],"existing":["Hitzeschutz"],"measures":[M("Heißzonen markieren","T (Technisch)")]},
        {"activity":"Kühl-/TK-Lager","hazard":"Kälte/Rutsch","sources":["Kühl/TK"],"existing":["Kälteschutz"],"measures":[M("Zeitbegrenzung/Matten")]},
        {"activity":"Reinigung/Desinfektion","hazard":"Chemische Belastung","sources":["Reiniger"],"existing":["PSA"],"measures":[M("SDB/Betriebsanweisungen an Station","T (Technisch)")]},
        {"activity":"Räuchern/Heißräuchern","hazard":"Rauch/Verbrennung/CO","sources":["Räucherkammer"],"existing":["Abluft"],"measures":[M("CO-Warnung nach Gefährdung","T (Technisch)")]},
    ],
    "Verkauf": [
        {"activity":"Aufschnitt/Bedienung","hazard":"Schnittverletzung","sources":["Aufschnitt"],"existing":["Schutzhaube"],"measures":[M("Messerschulung/Handschutz","Q (Qualifikation/Unterweisung)")]},
        {"activity":"Heißtheke","hazard":"Verbrennung","sources":["Heiße Theken"],"existing":["Hitzeschutz"],"measures":[M("Abdeckung/Abstellen sichern","T (Technisch)")]},
    ]
}

# ---------------------------
# KANTINE / GEMEINSCHAFTSVERPFLEGUNG
# ---------------------------
LIB_KANTINE = {
    "Küche": [
        {"activity":"Großkochgeräte/Kippkessel","hazard":"Verbrühung, Quetschung beim Kippen","sources":["Kippkessel"],"existing":["Hitzeschutz","2-Hand-Bed."],"measures":[M("Kipp-Prozess standardisieren")]},
        {"activity":"Tablettförderband/Spülstraße","hazard":"Einklemm-/Scherstellen, Heißwasser/Dampf","sources":["Bandspülmaschine"],"existing":["Abdeckungen","Not-Aus"],"measures":[M("Nur befugte Bedienung")]},
        {"activity":"Ausgabe/Frontcooking","hazard":"Verbrennung, Kontakt mit Gästen","sources":["Wärmebrücken","Pfannen"],"existing":["Abschirmung","Greifzonen"],"measures":[M("Abstand/Abschirmung","T (Technisch)")]},
        {"activity":"Regenerieren/Heißluftwagen","hazard":"Verbrennung, Dampf","sources":["Heißluftwagen"],"existing":["Hitzeschutz"],"measures":[M("Türöffnungsroutine"),M("Schutzhandschuhe","P (PSA)")]},
        {"activity":"Tablettsystem/Portionierung","hazard":"Schnitt/Verbrennung/Ergonomie","sources":["Tablettlinie"],"existing":["Organisation"],"measures":[M("Höhenanpassung/Wege freihalten")]},
    ],
    "Logistik": [
        {"activity":"Transportwagen/Tablettwagen","hazard":"Quetschen/Stolpern","sources":["Rollwagen","Aufzüge"],"existing":["Wege frei"],"measures":[M("Lastbegrenzung/Wegepriorität")]},
        {"activity":"Annahme/Kommissionierung","hazard":"Schnitt/Heben/Tragen","sources":["Kisten","Folien"],"existing":["Sichere Messer","Rollwagen"],"measures":[M("Sicherheitsmesser einsetzen","S (Substitution/Quelle entfernen)")]},
    ]
}

# ---------------------------
# KONDITOREI / CAFÉ
# ---------------------------
LIB_KONDITOREI = {
    "Produktion": [
        {"activity":"Zucker kochen/Karamell","hazard":"Heißsirup/Verbrennung","sources":["Kocher"],"existing":["Hitzeschutz"],"measures":[M("Schutzbrille & langsames Aufgießen","P (PSA)")]},
        {"activity":"Kuvertüre/Temperieren","hazard":"Hitze, Spritzer","sources":["Bad/Tempering"],"existing":["Hitzeschutz"],"measures":[M("Deckel/Spritzschutz","T (Technisch)")]},
        {"activity":"Kleingeräte/Rührwerke","hazard":"Scher-/Einklemmstellen","sources":["Rührwerk"],"existing":["Schutz","Not-Aus"],"measures":[M("Nur stromlos reinigen")]},
        {"activity":"Kühl-/TK","hazard":"Kälte/Rutsch","sources":["Kühl/TK"],"existing":["Kälteschutz"],"measures":[M("Aufenthalt begrenzen/Eis entfernen")]},
        {"activity":"Reinigung","hazard":"Chemikalien","sources":["Reiniger"],"existing":["PSA"],"measures":[M("Dosierhilfen/Betriebsanweisung","T (Technisch)")]},
    ],
    "Verkauf/Café": [
        {"activity":"Kaffeemaschine/Heißgetränke","hazard":"Verbrühung","sources":["Dampflanze"],"existing":["Hitzeschutz"],"measures":[M("Dampflanze abblasen")]},
        {"activity":"Tortenmesser/Glasvitrine","hazard":"Schnitt/Glasschaden","sources":["Glas","Messer"],"existing":["Sichere Entsorgung"],"measures":[M("Polier-/Schnittschutzhandschuhe","P (PSA)")]},
    ]
}

# ---------------------------
# BRAUEREI
# ---------------------------
LIB_BRAUEREI = {
    "Sudhaus": [
        {"activity":"Maischen/Kochen im Sudkessel","hazard":"Heißdampf/Verbrühung, CO₂ beim Kochen","sources":["Sudkessel","Whirlpool"],"existing":["Abschrankung","Hitzeschutz"],"measures":[M("Deckel & Dampfableitung prüfen","T (Technisch)"),M("Vorsicht beim Öffnen")]},
        {"activity":"Whirlpool/Trubabzug","hazard":"Heißdampf/Verbrennung","sources":["Whirlpool"],"existing":["Abdeckung"],"measures":[M("Öffnen nur nach Abkühlen")]},
        {"activity":"Läuterbottich","hazard":"Einsinken/Erstickung beim Einstieg, Heißdampf","sources":["Läuterbottich"],"existing":["Zutritt verboten"],"measures":[M("Befahren als enger Raum (Permit)")]},
        {"activity":"CIP-Reinigung","hazard":"Ätz-/Reizwirkung, Gasbildung","sources":["Laugen/Säuren"],"existing":["Dosierung","BA"],"measures":[M("CIP-Schläuche sichern","T (Technisch)"),M("Augendusche/Notdusche prüfen","T (Technisch)")]},
    ],
    "Gär-/Keller": [
        {"activity":"Gär-/Lagertanks","hazard":"CO₂-Ansammlung/Erstickung, Druck","sources":["Gärtank"],"existing":["CO₂-Warner","Lüftung"],"measures":[M("Warner testen & loggen","T (Technisch)"),M("Freimessen vor Einstieg")]},
        {"activity":"Druckbehälter/Überdruck","hazard":"Explosion/Druckverletzung","sources":["Tankdruck"],"existing":["Sicherheitsventile"],"measures":[M("SV-Prüfungen dokumentieren")]},
        {"activity":"Hefeernte/Umfüllen","hazard":"Biologische Gefährdung, Rutsch","sources":["Hefeschlamm"],"existing":["Handschutz"],"measures":[M("Spritzschutz & Kennzeichnung","T (Technisch)")]},
    ],
    "Abfüllung/Fasskeller": [
        {"activity":"Fassreinigung/Spülen","hazard":"CO₂/Restdruck, Chemie","sources":["Fasskeller"],"existing":["Druckentlastung"],"measures":[M("Entlüften/Spülen dokumentieren")]},
        {"activity":"Fassfüllen/Anstechen","hazard":"Druck, Schläge","sources":["Fass","ZKG"],"existing":["Sichere Kupplungen"],"measures":[M("Schlagschutz/PSA","P (PSA)")]},
    ],
    "Wartung/Technik": [
        {"activity":"CO₂-Flaschenlager","hazard":"Erstickung bei Leck","sources":["Flaschenbündel"],"existing":["CO₂-Warner","Belüftung"],"measures":[M("Dichtheitskontrolle")]},
        {"activity":"Ammoniak-Kälte","hazard":"NH₃-Toxizität/Leck","sources":["Kälteanlage"],"existing":["Gaswarnanlage"],"measures":[M("Alarm-/Rettungsplan"),M("Filter/Fluchtgeräte","P (PSA)")]},
    ],
}

# ---------------------------
# GETRÄNKEABFÜLLUNG
# ---------------------------
LIB_GETRAENKEABF = {
    "Sirupe/Konzentrat": [
        {"activity":"Ansatz Sirup","hazard":"Chemische Reizung (Säuren/Basen), Rutsch","sources":["Zutaten","CIP"],"existing":["Dosierhilfen"],"measures":[M("BA & SDB an Station","T (Technisch)")]},
        {"activity":"Zuckerhandling","hazard":"Staubexplosion (selten), Ergonomie","sources":["Zucker"],"existing":["Absaugung"],"measures":[M("Staubarme Beschickung","S (Substitution/Quelle entfernen)")]},
    ],
    "Gebindehandling": [
        {"activity":"Leergutannahme/Sortierung","hazard":"Scherben/Schnitt, Lärm","sources":["Kästen","Flaschen"],"existing":["Handschutz","Gehörschutz"],"measures":[M("Scherbenbeseitigung sofort"),M("Lärmmonitoring")]},
        {"activity":"Waschmaschine","hazard":"Heißlauge, Dampf","sources":["Flaschenwascher"],"existing":["Einhausung"],"measures":[M("Spritzschutz & Handschutz","P (PSA)")]},
    ],
    "Füller/Etikettierer": [
        {"activity":"Füllerbereich","hazard":"Quetschen, Drehteile, Reinigungschemie","sources":["Füller","Transportbänder"],"existing":["Schutzzäune","Lichtgitter"],"measures":[M("Interlocks prüfen","T (Technisch)")]},
        {"activity":"CO₂-/Kohlensäureversorgung","hazard":"Erstickung, Hochdruck","sources":["CO₂-Tank"],"existing":["CO₂-Warner"],"measures":[M("Sensorcheck + Lüftung","T (Technisch)")]},
    ],
    "Palettierung/Logistik": [
        {"activity":"Packen/Palettierer","hazard":"Einklemm-/Quetschstellen","sources":["Palettierer","Stretch"],"existing":["Schutzzonen"],"measures":[M("Sperrkreis & Freigabeprozesse")]},
        {"activity":"Flurförderzeuge","hazard":"Anfahren/Kollision","sources":["Stapler","Ameise"],"existing":["Wegeordnung"],"measures":[M("Staplerschein/Unterweisung","Q (Qualifikation/Unterweisung)")]},
    ]
}

# ---------------------------
# EISHERSTELLUNG
# ---------------------------
LIB_EIS = {
    "Produktion": [
        {"activity":"Pasteurisieren Milchmischung","hazard":"Verbrühung, Dampf","sources":["Pasteur"],"existing":["Hitzeschutz"],"measures":[M("Temperatur/Zeiten protokollieren")]},
        {"activity":"Homogenisieren/Mischen","hazard":"Einklemm-/Scherstellen","sources":["Homogenisator","Rührwerk"],"existing":["Schutzhauben"],"measures":[M("Reinigung nur stromlos")]},
        {"activity":"Gefrieren/Freezer","hazard":"Kälte/Erfrierung, Bewegte Teile","sources":["Kontifreezer"],"existing":["Abdeckungen"],"measures":[M("PSA Kälteschutz","P (PSA)")]},
        {"activity":"Aromen/Allergene","hazard":"Kreuzkontamination/Allergene","sources":["Nüsse","Milch"],"existing":["Allergenplan"],"measures":[M("Rein-/Unrein-Trennung")]},
        {"activity":"CIP-Reinigung","hazard":"Säuren/Laugen","sources":["CIP"],"existing":["Dosierung"],"measures":[M("Augendusche/Notdusche","T (Technisch)")]},
    ],
    "Verkauf/Theke": [
        {"activity":"Eistheke/Spatel","hazard":"Biologische Risiken, Temperaturkette","sources":["Theke"],"existing":["Temperaturkontrolle"],"measures":[M("Stichproben/Protokoll")]},
        {"activity":"Waffeleisen/Heißgeräte","hazard":"Verbrennung","sources":["Waffeleisen"],"existing":["Hitzeschutz"],"measures":[M("Handschutz bereit","P (PSA)")]},
    ],
    "Lager": [
        {"activity":"TK-Lager -30°C","hazard":"Kälte, Rutsch","sources":["TK"],"existing":["Kälteschutz"],"measures":[M("Max. Aufenthaltsdauer/Partnerprinzip")]},
    ]
}

# ---------------------------
# EVENT / CATERING
# ---------------------------
LIB_EVENT = {
    "Vorbereitung/Produktion": [
        {"activity":"Mise en place/Kochen vor Ort","hazard":"Verbrennung/Verbrühung, Elektrik mobil","sources":["Induktionsfelder","Gasbrenner"],"existing":["E-Check mobil"],"measures":[M("Zuleitungen sichern"),M("Feuerlöscher bereit")]},
        {"activity":"Verladen/Transport","hazard":"Quetschung/Heben/Tragen","sources":["Kisten","GN-Behälter"],"existing":["Rollwagen"],"measures":[M("Ladungssicherung")]},
        {"activity":"Kühlkette/Mobile Kühlung","hazard":"Verderb/biologische Risiken","sources":["Kühlboxen","Fahrzeuge"],"existing":["Temperaturkontrolle"],"measures":[M("Datenlogger einsetzen","T (Technisch)")]},
    ],
    "Aufbau/Betrieb": [
        {"activity":"Zelte/Provisorien","hazard":"Wind/Absturz/Stolpern","sources":["Zelt","Kabel"],"existing":["Abspannung","Kabelbrücken"],"measures":[M("Abnahme/Prüfbuch Zelt/Aggregat")]},
        {"activity":"Stromerzeuger/Aggregate","hazard":"CO/Abgase, Lärm, Stromschlag","sources":["Generator"],"existing":["Abstand/Lüftung"],"measures":[M("Erdung/PRCD-S","T (Technisch)"),M("CO-Warnung in Gebäuden","T (Technisch)")]},
        {"activity":"Ausgabe/Frontcooking","hazard":"Kontakt Gäste, heiße Flächen","sources":["Rechauds","Pfannen"],"existing":["Abschirmung"],"measures":[M("Greifzonen/Barriere","T (Technisch)")]},
    ],
    "Abbau/Reinigung": [
        {"activity":"Heißgeräte abbauen","hazard":"Verbrennung/Restwärme","sources":["Geräte"],"existing":["Abkühlen"],"measures":[M("Schnittschutzhandschuhe beim Packen","P (PSA)")]},
    ]
}

# ---------------------------
# FAST FOOD / QUICKSERVICE
# ---------------------------
LIB_QSR = {
    "Küche": [
        {"activity":"Fritteusenbetrieb","hazard":"Fettbrand, Verbrennung","sources":["Fritteuse"],"existing":["Löschdecke"],"measures":[M("Autom. Löschanlage prüfen","T (Technisch)"),M("Kein Wasser!")]},
        {"activity":"Griddle/Flame Broiler","hazard":"Hitze/Verbrennung, Rauch","sources":["Grill"],"existing":["Abzug"],"measures":[M("Reinigungsplan Haube/Filter")]},
        {"activity":"Slicer/Chopper","hazard":"Schnitt/Scherstellen","sources":["Slicer"],"existing":["Schutz"],"measures":[M("Nur mit Werkzeug reinigen")]},
        {"activity":"Gefriertruhe/Schockfroster","hazard":"Kälte/Rutsch","sources":["TK"],"existing":["Kälteschutz"],"measures":[M("Eis entfernen")]},
        {"activity":"Bestellung/Allergene","hazard":"Fehlbestellung/Allergischer Schock","sources":["Kasse","App"],"existing":["Allergenliste"],"measures":[M("Allergen-Abfrage im Prozess")]},
    ],
    "Service": [
        {"activity":"Drive-Thru","hazard":"Fahrzeugkontakt/Abgase/Lärm","sources":["Fahrspur"],"existing":["Markierung"],"measures":[M("Reflexwesten/Visibility","P (PSA)")]},
        {"activity":"Getränkespender/CO₂","hazard":"Erstickung/Hochdruck","sources":["CO₂-Flaschen"],"existing":["Befestigung"],"measures":[M("Sensorentest/Wechselprozess")]},
    ],
    "Reinigung": [
        {"activity":"Schaum-/Sprühreinigung","hazard":"Aerosole/Chemie","sources":["Reiniger"],"existing":["PSA"],"measures":[M("Schaumlanze statt Spray","S (Substitution/Quelle entfernen)")]},
    ]
}

# ---------------------------
# WÄSCHEREI / TEXTILREINIGUNG
# ---------------------------
LIB_WAESCHE = {
    "Annahme/Vorsortierung": [
        {"activity":"Schmutzwäscheannahme","hazard":"Biologische Gefährdungen, Stichverletzung","sources":["Schmutzwäsche"],"existing":["Handschutz"],"measures":[M("Sharps-Check/Trennung Unrein/Rein")]},
        {"activity":"Sortieren/Wiegen","hazard":"Heben/Tragen/Staub","sources":["Säcke","Wäschewagen"],"existing":["Hebehilfen"],"measures":[M("Absaugung an Entleerer","T (Technisch)")]},
    ],
    "Waschen/Nassreinigung": [
        {"activity":"Maschinenbeschickung","hazard":"Einklemm-/Scherstellen, Heißwasser/Dampf","sources":["Waschmaschinen"],"existing":["Not-Aus"],"measures":[M("Türverriegelungen prüfen","T (Technisch)")]},
        {"activity":"Chemiedosierung","hazard":"Ätz-/Reizwirkung","sources":["Flüssigchemie"],"existing":["Dosieranlage"],"measures":[M("Schlauch-/Kopplungscheck")]},
    ],
    "Finish/Trocknen/Mangeln": [
        {"activity":"Trockner/Mangel","hazard":"Einzugs-/Quetschstellen, Hitze","sources":["Tumbler","Mangel"],"existing":["Hauben","Zweihand"],"measures":[M("Einzugsabstand/Notleinen prüfen","T (Technisch)")]},
        {"activity":"Bügeln/Dampf","hazard":"Verbrühung/Verbrennung","sources":["Dampfbügel"],"existing":["Hitzeschutz"],"measures":[M("Dampfschläuche prüfen")]},
    ],
    "Reparatur/Nähen": [
        {"activity":"Nähmaschinenarbeit","hazard":"Nadelstich/Ergonomie","sources":["Nähmaschine"],"existing":["Fingerschutz"],"measures":[M("Beleuchtung/Arbeitshöhe anpassen","T (Technisch)")]},
    ],
}

# ---------------------------
# NEUE BRANCHEN
# ---------------------------

# Brauereigaststätte (Küche + Schank + Keller)
LIB_BRAUGAST = {
    "Küche": [
        {"activity":"Brauhaus-Grill/Flamme","hazard":"Verbrennung, Rauch","sources":["Grill","Holzkohle"],"existing":["Abzug"],"measures":[M("Funkenflug verhindern","T (Technisch)")]},
        {"activity":"Brezeln/Ofen","hazard":"Hitze/Verbrennung","sources":["Ofen"],"existing":["Hitzeschutz"],"measures":[M("Ofenhandschuhe","P (PSA)")]},
    ],
    "Schank/Keller": [
        {"activity":"Fasswechsel/Anstechen","hazard":"Druck/Schläge/CO₂","sources":["Fässer"],"existing":["Kupplungen"],"measures":[M("Wechsel zu zweit"),M("CO₂-Warner testen","T (Technisch)")]},
        {"activity":"Leitungsreinigung","hazard":"Chemikalien/CO₂-Reste","sources":["Schankanlage"],"existing":["BA/Absperren"],"measures":[M("Freimessen bei Schächten")]},
    ],
    "Service": [
        {"activity":"Tabletttragen/Gänge","hazard":"Überlastung/Stolpern","sources":["Tabletts","Treppen"],"existing":["Rutschschutz"],"measures":[M("Wege freihalten")]},
    ],
}

# Fisch/Seafood-Verarbeitung
LIB_FISCH = {
    "Produktion": [
        {"activity":"Filetieren/Entgräten","hazard":"Schnitt, Stich","sources":["Filetiermesser"],"existing":["Schnittschutz"],"measures":[M("Schnittschutzhandschuhe","P (PSA)")]},
        {"activity":"Eis/Glasurieren","hazard":"Kälte/Rutsch","sources":["Flockeneis"],"existing":["Kälteschutz"],"measures":[M("Rutschhemmende Matten","T (Technisch)")]},
        {"activity":"Räuchern (kalt/heiß)","hazard":"Rauch/CO/Verbrennung","sources":["Räucherkammer"],"existing":["Abluft"],"measures":[M("CO-Warnung falls erforderlich","T (Technisch)")]},
        {"activity":"Schuppen/Entschleimen","hazard":"Biologische Aerosole/Rutsch","sources":["Fischreste"],"existing":["Hygieneplan"],"measures":[M("Spritzschutz/Absaugung","T (Technisch)")]},
        {"activity":"Reinigung/Desinfektion","hazard":"Chemikalien/Ätzwirkung","sources":["Reiniger"],"existing":["PSA"],"measures":[M("Dosierhilfe & BA","T (Technisch)")]},
    ],
    "Lager/Logistik": [
        {"activity":"TK-Lager -25°C","hazard":"Kälte/Rutsch","sources":["TK"],"existing":["Kälteschutz"],"measures":[M("Zeitbegrenzung/Partnerprinzip")]},
        {"activity":"Kisten/Paletten bewegen","hazard":"Quetschen/Heben/Tragen","sources":["EPS-Kisten","Paletten"],"existing":["Hubhilfe"],"measures":[M("Lastgrenzen & Wegeordnung")]},
    ],
}

# Getränke-Großhandel / Logistik
LIB_GETR_GH = {
    "Lager": [
        {"activity":"Kommissionierung","hazard":"Heben/Tragen/Quetschen","sources":["Kisten","Paletten"],"existing":["Ameise/Stapler"],"measures":[M("Hebezeug nutzen")]},
        {"activity":"Leergutannahme","hazard":"Scherben/Lärm","sources":["Flaschen"],"existing":["Gehör-/Handschutz"],"measures":[M("Scherbenmanagement/Kehrzeug")]},
        {"activity":"Hochregal/Stapler","hazard":"Absturz/Anfahren","sources":["Stapler"],"existing":["Zonen/Wege"],"measures":[M("Staplerschein/UVV","Q (Qualifikation/Unterweisung)")]},
    ],
    "Transport": [
        {"activity":"LKW Be-/Entladung","hazard":"Absturz Rampe/Quetschung","sources":["Rampe","Ladebordwand"],"existing":["Stopper","Absperrung"],"measures":[M("Sicherung gegen Wegrollen","T (Technisch)")]},
        {"activity":"Auslieferung","hazard":"Verkehrsunfall/Überfall","sources":["Straße"],"existing":["Fahrerschulung"],"measures":[M("Ladungssicherung/Antirutsch","T (Technisch)")]},
    ],
}

# Pizzeria
LIB_PIZZERIA = {
    "Küche": [
        {"activity":"Pizzaofen/Holzofen","hazard":"Strahlungswärme/Verbrennung","sources":["Ofen"],"existing":["Hitzeschutz"],"measures":[M("Ofenhandschuhe/Schieber","P (PSA)")]},
        {"activity":"Teigportionierer/Pressen","hazard":"Quetsch/Scherstellen","sources":["Portionierer","Presse"],"existing":["Schutz"],"measures":[M("Nur stromlos reinigen")]},
        {"activity":"Aufschnitt/Gemüse","hazard":"Schnitt","sources":["Messer","Slicer"],"existing":["Schutz"],"measures":[M("Schnittschutz bei Bedarf","P (PSA)")]},
        {"activity":"Tomatensauce/Heißgeräte","hazard":"Spritzer/Verbrühung","sources":["Töpfe"],"existing":["Deckel"],"measures":[M("Spritzschutz nutzen","T (Technisch)")]},
    ],
    "Service/Lieferung": [
        {"activity":"Lieferdienst Roller/PKW","hazard":"Verkehrsunfall/Witterung","sources":["Straße"],"existing":["Schutzausrüstung"],"measures":[M("Fahrerschulung/Defensiv")]},
        {"activity":"Thermoboxen","hazard":"Verbrennung/Ergonomie","sources":["Heißboxen"],"existing":["PSA"],"measures":[M("Tragewege kurz halten")]},
    ]
}

# Sushi-Bar
LIB_SUSHI = {
    "Küche": [
        {"activity":"Rohfisch-Verarbeitung","hazard":"Biologische Risiken/Parasiten","sources":["Rohfisch"],"existing":["Gefrierbehandlung/Temperaturkette"],"measures":[M("Wareneingangskontrolle/Temperaturprotokoll")]},
        {"activity":"Messer/Schneiden","hazard":"Schnittverletzung","sources":["Messer"],"existing":["Scharfe Messer"],"measures":[M("Schnittschutz bei Bedarf","P (PSA)")]},
        {"activity":"Reisbereiter/Dampf","hazard":"Verbrühung","sources":["Reiskocher"],"existing":["Hitzeschutz"],"measures":[M("Deckel vorsichtig öffnen")]},
        {"activity":"Allergene (Soja, Sesam, Fisch)","hazard":"Allergische Reaktionen","sources":["Zutaten"],"existing":["Kennzeichnung"],"measures":[M("Trennung/Utensilien farblich")]},
    ],
    "Service": [
        {"activity":"Thekenarbeit/Messer","hazard":"Schnitt/Publikumsnähe","sources":["Theke"],"existing":["Abschirmung"],"measures":[M("Sichere Übergabe/Schnittbereich abgrenzen")]},
    ]
}

# Café/Bar eigenständig
LIB_CAFE_BAR = {
    "Bar": [
        {"activity":"Siebräger/Heißwasser","hazard":"Verbrühung","sources":["Espressomaschine"],"existing":["Hinweise"],"measures":[M("Dampflanze abblasen")]},
        {"activity":"Glaspolitur","hazard":"Schnitt","sources":["Glas"],"existing":["Entsorgung"],"measures":[M("Polierhandschuhe","P (PSA)")]},
        {"activity":"CO₂/Kälteanlage","hazard":"Erstickung/Leck","sources":["CO₂","Kühlzelle"],"existing":["Gaswarner"],"measures":[M("Sensorentest protokollieren","T (Technisch)")]},
    ],
    "Backoffice": [
        {"activity":"Kassenabschluss/Geldtransport","hazard":"Überfall/Stress","sources":["Kasse"],"existing":["Sichere Wege"],"measures":[M("Zwei-Personen-Regel nach Risiko")]},
    ]
}

INDUSTRY_LIBRARY: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
    "Hotel/Gastgewerbe": LIB_HOTEL,
    "Bäckerei": LIB_BAECKEREI,
    "Fleischerei/Metzgerei": LIB_FLEISCHEREI,
    "Gemeinschaftsverpflegung/Kantine": LIB_KANTINE,
    "Konditorei/Café": LIB_KONDITOREI,
    "Brauerei": LIB_BRAUEREI,
    "Brauereigaststätte": LIB_BRAUGAST,
    "Fisch/Seafood-Verarbeitung": LIB_FISCH,
    "Getränkeabfüllung": LIB_GETRAENKEABF,
    "Getränke-Großhandel/Logistik": LIB_GETR_GH,
    "Eisherstellung": LIB_EIS,
    "Event/Catering": LIB_EVENT,
    "Fast Food/Quickservice": LIB_QSR,
    "Wäscherei/Textilreinigung": LIB_WAESCHE,
    "Pizzeria": LIB_PIZZERIA,
    "Sushi-Bar": LIB_SUSHI,
    "Café/Bar (eigenständig)": LIB_CAFE_BAR,
}

# =========================
# Vorlagen laden/auswählen
# =========================

def add_template_items(assess: Assessment, template: Dict[str, List[Dict[str, Any]]],
                       selected_keys: Optional[List[str]] = None, industry_name: Optional[str] = None):
    for area, items in template.items():
        for item in items:
            key = template_item_key(industry_name or assess.industry, area, item)
            if selected_keys is not None and key not in selected_keys:
                continue
            hz = Hazard(
                id=new_id(), area=area, activity=item["activity"], hazard=item["hazard"],
                sources=item.get("sources", []), existing_controls=item.get("existing", [])
            )
            for m in item.get("measures", []):
                hz.additional_measures.append(Measure(
                    title=m["title"], stop_level=m["stop_level"], notes=m.get("notes","")
                ))
            assess.hazards.append(hz)

def preload_industry(assess: Assessment, industry_name: str, replace: bool = True):
    assess.industry = industry_name
    if replace:
        assess.hazards = []
    template = INDUSTRY_LIBRARY.get(industry_name, {})
    add_template_items(assess, template, selected_keys=None, industry_name=industry_name)

def template_item_key(industry: str, area: str, item: Dict[str, Any]) -> str:
    return slug(industry, area, item.get("activity",""), item.get("hazard",""))

def iter_template_items(industry: str) -> List[Tuple[str, Dict[str, Any], str]]:
    lib = INDUSTRY_LIBRARY.get(industry, {})
    out = []
    for area, items in lib.items():
        for it in items:
            out.append((area, it, template_item_key(industry, area, it)))
    return out

# =========================
# Streamlit App
# =========================

st.set_page_config(page_title="Gefährdungsbeurteilung – Branchen (BGN) mit Auswahl", layout="wide")

# Session init
if "assessment" not in st.session_state or st.session_state.get("assessment") is None:
    st.session_state.assessment = Assessment(
        company="Musterbetrieb GmbH", location="Beispielstadt",
        created_at=date.today().isoformat(), created_by="HSE/SiFa",
        industry="Hotel/Gastgewerbe",
    )
    preload_industry(st.session_state.assessment, "Hotel/Gastgewerbe", replace=True)

assess: Assessment = st.session_state.assessment

# Kopf
col_head1, col_head2 = st.columns([0.8, 0.2])
with col_head1:
    st.title("Gefährdungsbeurteilung – Branchen (BGN) mit Checkbox-Auswahl")
with col_head2:
    if st.button("📄 Duplizieren", key="btn_duplicate"):
        assess.created_at = date.today().isoformat()
        assess.company = f"{assess.company} (Kopie)"
        st.success("Kopie erstellt. Bitte speichern/exportieren.")

st.caption("Struktur: Vorlagen auswählen → Vorbereiten → Ermitteln → Beurteilen → Maßnahmen → Umsetzen → Wirksamkeit → Dokumentieren → Fortschreiben")

# Sidebar
with st.sidebar:
    st.header("Stammdaten")
    assess.company = st.text_input("Unternehmen", assess.company, key="meta_company")
    assess.location = st.text_input("Standort", assess.location, key="meta_location")
    assess.created_by = st.text_input("Erstellt von", assess.created_by, key="meta_created_by")
    assess.created_at = st.text_input("Erstellt am (ISO)", assess.created_at, key="meta_created_at")

    st.markdown("---")
    st.subheader("Branche wählen (für Vorlagen)")
    options = list(INDUSTRY_LIBRARY.keys())
    current_industry = getattr(assess, "industry", None) or "Hotel/Gastgewerbe"
    default_idx = options.index(current_industry) if current_industry in options else 0
    sector = st.selectbox("Branche", options=options, index=default_idx, key="sel_industry")
    st.caption(f"Aktuell geladen: **{assess.industry}**")

    # Schnell-Laden (Sidebar)
    st.markdown("**Schnell laden:**")
    c_load1, c_load2 = st.columns(2)
    with c_load1:
        if st.button("📚 Vorlage ERSETZEN", key="btn_load_replace_sidebar"):
            assess.hazards = []
            tmpl = INDUSTRY_LIBRARY.get(sector, {})
            add_template_items(assess, tmpl, selected_keys=None, industry_name=sector)
            assess.industry = sector
            if "template_checks" in st.session_state:
                st.session_state.template_checks = {}
            st.success(f"Vorlage '{sector}' geladen (ersetzt).")
            st.rerun()
    with c_load2:
        if st.button("➕ Vorlage ANHÄNGEN", key="btn_load_append_sidebar"):
            tmpl = INDUSTRY_LIBRARY.get(sector, {})
            add_template_items(assess, tmpl, selected_keys=None, industry_name=sector)
            assess.industry = sector
            st.success(f"Vorlage '{sector}' hinzugefügt (angehängt).")
            st.rerun()

    st.markdown("---")
    st.subheader("Risikomatrix (5×5)")
    thr = assess.risk_matrix_thresholds.get("thresholds", [6, 12, 16])
    low = st.number_input("Grenze niedrig (≤)", min_value=2, max_value=10, value=int(thr[0]), key="thr_low")
    mid = st.number_input("Grenze mittel (≤)", min_value=low+1, max_value=16, value=int(thr[1]), key="thr_mid")
    high = st.number_input("Grenze hoch (≤)", min_value=mid+1, max_value=24, value=int(thr[2]), key="thr_high")
    assess.risk_matrix_thresholds["thresholds"] = [low, mid, high]

    st.markdown("---")
    st.subheader("Export / Speicher")
    if st.button("📥 JSON sichern (Download unten aktualisieren)", key="btn_json_dump"):
        st.session_state["json_blob"] = as_json(assess)
    json_blob = st.session_state.get("json_blob", as_json(assess))
