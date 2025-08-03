from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from bs4 import BeautifulSoup
from openai import OpenAI
from dotenv import load_dotenv

import time
import csv
import requests
import os
import subprocess


client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

out_dir = "output\\"

scrape_dir = out_dir + "scrapeyard"
promt_dir = out_dir + "aifood"
results_dir = out_dir + "airesult"

search_keyword = "Neueintragung"


def init():
    # Create needed dirs
    os.makedirs(scrape_dir, exist_ok=True)  
    os.makedirs(promt_dir, exist_ok=True)  
    os.makedirs(results_dir, exist_ok=True)  

def getSoupData():
    # Open browser
    driver = webdriver.Chrome()
    driver.get("https://www.shab.ch/#!/search/publications")

    # Wait till loaded
    time.sleep(5)

    # Add keyword into the search field
    search_input = driver.find_element(By.ID, "keyword")
    search_input.send_keys(search_keyword)
    search_input.send_keys(Keys.ENTER)

    # Wait till loaded
    time.sleep(5)

    # get html 
    html = driver.page_source

    # close brower
    driver.quit()

    return BeautifulSoup(html, "html.parser")


def getLinkData(soup):
    linkData = []
    entries = soup.select("div.list-entry.list-entry-tenant")

    for entry in entries:
        link_tag = entry.select_one("a")
        title = link_tag.text.strip()
        href = link_tag.get("href")

        linkData.append([title, "https://www.shab.ch/" + href]); 

    return linkData


def donwloadXML(linkDataList):
    # open browser
    driver = webdriver.Chrome()
    time.sleep(5)
    
    for element in linkDataList:
        
        found = False
        retry = False

        # link list entry properties
        title = element[0]
        detail_url = element[1]

        # open site, wait till loaded
        driver.get(detail_url)
        time.sleep(0.1)

        detail_html = driver.page_source
        detail_soup = BeautifulSoup(detail_html, "html.parser")
        
        while not found:
            # Find XML Link
            xml_link_tag = detail_soup.select_one('a.cmp-link-with-icon[href$="/xml"]')
            if xml_link_tag:
                xml_href = xml_link_tag.get("href")
                xml_url = "https://www.shab.ch" + xml_href

                # XML download
                response = requests.get(xml_url)
                filename = f"{title[:50].replace(' ', '_')}.xml"
                filename = os.path.join(scrape_dir, filename)

                with open(filename, "wb") as f:
                    f.write(response.content)
                print(f"✓ XML gespeichert: {filename}")
                found = True
            else:
                # only one retry
                if not retry:
                    retry = True
                    # if not found, retry with 5 sec delay
                    time.sleep(5)
                    print(f"✗ Kein XML-Link gefunden für {title}")
                else: 
                    break
        driver.quit()


def writeLinkList(linkDataList):
    with open(out_dir + 'ausgabe.csv', 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerows(linkDataList)


def saveResult(result_text, base_name):
    # Analyse speichern
    result_filename = os.path.join(results_dir, f"{base_name}_analysis.md")

    with open(result_filename, "w", encoding="utf-8") as res_file:
        res_file.write(result_text)


def prepareXMLforPrompt():
    
    promptData = []
    # All files in the scrape dir
    for filename in os.listdir(scrape_dir):
        if filename.endswith(".xml"):
            xml_path = os.path.join(scrape_dir, filename)
            try:
                with open(xml_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    soup = BeautifulSoup(content, "xml")
            except Exception as e:
                print(f"✗ Fehler bei Datei {filename}: {e}")
                continue

            if soup.find("language").text == "de":
                #collect relevant data for prompt
                entry = {
                    "language": soup.find("language").text if soup.find("language") else None,
                    "publicationDate": soup.find("publicationDate").text if soup.find("publicationDate") else None,
                    "title_de": soup.find("title").find("de").text if soup.find("title") and soup.find("title").find("de") else None,
                    "journalDate": soup.find("journalDate").text if soup.find("journalDate") else None,
                    "publicationText": soup.find("publicationText").text if soup.find("publicationText") else None,
                    "name": soup.find("name").text if soup.find("name") else None,
                    "uid": soup.find("uidOrganisationId").text if soup.find("uidOrganisationId") else None,
                    "seat": soup.find("seat").text if soup.find("seat") else None,
                    "legalForm": soup.find("legalForm").text if soup.find("legalForm").text else None,
                    "address": {
                        "street": soup.find("street").text if soup.find("street") else None,
                        "houseNumber": soup.find("houseNumber").text if soup.find("houseNumber") else None,
                        "swissZipCode": soup.find("swissZipCode").text if soup.find("swissZipCode") else None,
                        "town": soup.find("town").text if soup.find("town") else None,
                    },
                    "capital": {
                        "nominal": soup.find("nominal").text if soup.find("nominal") else None,
                        "paid": soup.find("paid").text if soup.find("paid") else None,
                    }
                }
                promptData.append(entry)

    return promptData
            

def generate_prompt_from_data(entry):
    """
    Nimmt ein Dictionary mit extrahierten Firmendaten entgegen und erstellt einen kompakten Prompt.
    """
    # Schöne Formatierung der Felddaten (inkl. Adresse & Kapital)
    def get_address(addr):
        return ", ".join(filter(None, [
            addr.get("street", ""), 
            addr.get("houseNumber", ""), 
            addr.get("swissZipCode", ""), 
            addr.get("town", "")
        ]))

    def get_capital(cap):
        try:
            return f"CHF {float(cap.get('paid', 0)):,.0f}".replace(",", "'")
        except:
            return "unbekannt"

    xml_info_md = f"""**Name:** {entry.get('name', '')}  
                        **Adresse:** {get_address(entry.get('address', {}))}  
                        **Sitz:** {entry.get('seat', '')}  
                        **UID:** {entry.get('uid', '')}  
                        **Rechtsform:** {entry.get('legalForm', '')}  
                        **Publikationsdatum:** {entry.get('publicationDate', '')}  
                        **Kapital:** {get_capital(entry.get('capital', {}))}  
                        **Zweck:** {entry.get('publicationText', '').strip()}"""

    # Prompt-Template (minimalistisch, klar strukturiert)
    prompt = f"""Ich lade dir strukturierte Handelsregisterdaten (SHAB-Eintrag) zu einem Unternehmen hoch.
        Bitte:
        1. Extrahiere alle relevanten Firmendaten aus dem XML (Name, UID, Adresse, Zweck, Personen).
        2. Recherchiere online:
        - Website, Telefonnummer, E-Mail
        - Social Media, Branchenverzeichnisse, Artikel
        3. Beurteile den Stand des Unternehmens (neu, aktiv, sichtbar?).
        4. Schlage eine mögliche Akquise-Ansprache und passende Leistungen vor (z. B. Web, Prozesse, Marketing).

        Wichtig:
        1. Antwort im Markdown-Format
        2. Redundanzen vermeiden
        3. Zweckmässig, kurz gehalten, informativ
        4. Kontaktangaben suchen im Internet, überall

        ```xml
        {xml_info_md}
                        """
    return prompt


def consultAI(prompt):
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7
    )
    return response



########################## MAIN SCRIPT ##########################

init()
soup = getSoupData()
linkDataList = getLinkData(soup)

writeLinkList(linkDataList)

donwloadXML(linkDataList)

promtDetails = prepareXMLforPrompt()

for element in promtDetails:
    prompt = generate_prompt_from_data(element)
    result = consultAI(prompt)
    saveResult(result.choices[0].message.content, element["title_de"])



