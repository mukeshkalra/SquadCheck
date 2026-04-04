#!/usr/bin/env python3
"""Generates pipeline/data/squads.json from hard-coded squad data."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
OUT  = ROOT / "pipeline" / "data" / "squads.json"

GROUPS = {
    "A": ["Mexico", "South Korea", "South Africa", "Czechia"],
    "B": ["Canada", "Bosnia and Herzegovina", "Qatar", "Switzerland"],
    "C": ["Brazil", "Morocco", "Haiti", "Scotland"],
    "D": ["USA", "Paraguay", "Australia", "Türkiye"],
    "E": ["Germany", "Curaçao", "Ivory Coast", "Ecuador"],
    "F": ["Netherlands", "Japan", "Sweden", "Tunisia"],
    "G": ["Belgium", "Egypt", "Cape Verde", "Saudi Arabia"],
    "H": ["Spain", "New Zealand", "Senegal", "Jordan"],
    "I": ["France", "Norway", "Algeria", "Austria"],
    "J": ["Portugal", "DR Congo", "Uzbekistan", "Colombia"],
    "K": ["England", "Croatia", "Iran", "Ghana"],
    "L": ["Argentina", "Uruguay", "Panama", "Iraq"],
}

CONFEDERATIONS = {
    "USA": "CONCACAF", "Mexico": "CONCACAF", "Canada": "CONCACAF",
    "Haiti": "CONCACAF", "Panama": "CONCACAF", "Curaçao": "CONCACAF",
    "England": "UEFA", "France": "UEFA", "Spain": "UEFA", "Germany": "UEFA",
    "Portugal": "UEFA", "Netherlands": "UEFA", "Belgium": "UEFA", "Croatia": "UEFA",
    "Switzerland": "UEFA", "Austria": "UEFA", "Scotland": "UEFA", "Norway": "UEFA",
    "Bosnia and Herzegovina": "UEFA", "Sweden": "UEFA", "Türkiye": "UEFA", "Czechia": "UEFA",
    "Argentina": "CONMEBOL", "Brazil": "CONMEBOL", "Uruguay": "CONMEBOL",
    "Colombia": "CONMEBOL", "Ecuador": "CONMEBOL", "Paraguay": "CONMEBOL",
    "Japan": "AFC", "South Korea": "AFC", "Australia": "AFC",
    "Saudi Arabia": "AFC", "Iran": "AFC", "Iraq": "AFC",
    "Uzbekistan": "AFC", "Jordan": "AFC", "Qatar": "AFC",
    "Morocco": "CAF", "Senegal": "CAF", "South Africa": "CAF",
    "Egypt": "CAF", "Algeria": "CAF", "Ghana": "CAF",
    "Ivory Coast": "CAF", "Tunisia": "CAF", "Cape Verde": "CAF", "DR Congo": "CAF",
    "New Zealand": "AFC",
}

def pl(pos, *names):
    return [{"name": n, "pos": pos} for n in names]

RAW_SQUADS = {
"Mexico": (
    pl("GK", "Malagón", "Ochoa", "González") +
    pl("DF", "Montes", "Vásquez", "Sanchez", "Angulo", "J. Araujo", "Guzman", "Reyes") +
    pl("MF", "Alvarez", "Chávez", "Pineda", "Romo", "Rodriguez", "Mora", "Beltrán") +
    pl("FW", "Giménez", "Quiñones", "Lozano", "Huerta", "Antuna", "Martin", "Alvarado", "Vega")
),
"South Korea": (
    pl("GK", "Jo Hyeon-woo", "Song Bum-keun", "Hwang In-beom") +
    pl("DF", "Kim Min-jae", "Cho Yu-min", "Kim Jin-su", "Seol Young-woo", "Kim Young-gwon", "Jung Seung-hyun", "Kwon Kyung-won", "Lee Myung-jae") +
    pl("MF", "Hwang In-beom", "Lee Kang-in", "Park Seung-ho", "Paik Seung-ho", "Hong Hyun-seok", "Lee Jae-sung", "Jeong Woo-yeong") +
    pl("FW", "Son Heung-min", "Hwang Hee-chan", "Cho Gue-sung", "Oh Hyeon-gyu", "Joo Min-kyu", "Yang Hyun-jun", "Bae Jun-ho", "Um Won-sang")
),
"South Africa": (
    pl("GK", "Ronwen Williams", "Bruce Bvuma", "Veli Mothwa") +
    pl("DF", "Nyiko Mobbie", "Sifiso Hlanti", "Teboho Mokoena", "Mothobi Mvala", "Nkosinathi Sibisi", "Aubrey Modiba", "Siyanda Xulu", "Khuliso Mudau") +
    pl("MF", "Themba Zwane", "Yusuf Maart", "Ethan Bristow", "Sphephelo Sithole", "Keagan Dolly", "Itumeleng Khune") +
    pl("FW", "Percy Tau", "Lyle Foster", "Evidence Makgopa", "Terrence Mashego", "Bongokuhle Hlongwane", "Lebo Mothiba", "Sipho Mbule", "Oswin Appollis", "Lerby Rayners", "Kermit Erasmus")
),
"Czechia": (
    pl("GK", "Jindřich Staněk", "Matěj Kovář", "Jiří Laštůvka") +
    pl("DF", "Vladimír Coufal", "Tomáš Holeš", "Lukáš Hrádecký", "David Jurásek", "Martin Vitík", "David Zima", "Jan Bořil", "Robin Hranáč") +
    pl("MF", "Tomáš Souček", "Antonín Barák", "Lukáš Provod", "Matěj Jurásek", "Ondřej Lingr", "Michal Sadílek", "Adam Hložek") +
    pl("FW", "Patrik Schick", "Tomáš Chorý", "Jan Kuchta", "Mojmír Chytil", "Ondřej Čvančara", "Václav Jemelka", "Jakub Pešek", "Adam Hložek")
),
"Canada": (
    pl("GK", "Maxime Crépeau", "Milan Borjan", "Dayne St. Clair") +
    pl("DF", "Alistair Johnston", "Kamal Miller", "Derek Cornelius", "Richie Laryea", "Steven Vitória", "Doneil Henry", "Marcus Godinho", "Samuel Adekugbe") +
    pl("MF", "Stephen Eustáquio", "Ismaël Koné", "Jonathan Osorio", "Mathieu Choinière", "Samuel Piette", "Charles-Andreas Brym") +
    pl("FW", "Jonathan David", "Tajon Buchanan", "Jacob Shaffelburg", "Cyle Larin", "Liam Millar", "Theo Bair", "Jayden Nelson", "Junior Hoilett", "Antoine Semenyo")
),
"Bosnia and Herzegovina": (
    pl("GK", "Ibrahim Šehić", "Nikola Vasilj", "Jasmin Handžić") +
    pl("DF", "Ognjen Vranješ", "Amar Dedić", "Ermedin Demirović", "Dario Šimić", "Sead Kolašinac", "Mergim Berisha", "Saša Marković", "Nikola Milenković") +
    pl("MF", "Smail Prevljak", "Amer Gojak", "Ermin Bičakčić", "Haris Hajradinović", "Dario Saric", "Zajko Zeba", "Kenan Pirić") +
    pl("FW", "Edin Džeko", "Ermedin Demirović", "Anel Ahmedhodžić", "Tarik Đulović", "Vedat Muriqi", "Rijad Bajić", "Darko Todorović", "Tino-Sven Sušić")
),
"Qatar": (
    pl("GK", "Meshaal Barsham", "Saad Al Sheeb", "Yusuf Hassan") +
    pl("DF", "Pedro Miguel", "Tarek Salman", "Bassam Al-Rawi", "Abdulkarim Hassan", "Mohammed Waad", "Khaled Narey", "Karim Boudiaf", "Musab Kheder") +
    pl("MF", "Akram Afif", "Hassan Al-Haydos", "Karim Boudiaf", "Assim Madibo", "Mohammed Muntari", "Ali Almoez", "Ahmed Fatehi") +
    pl("FW", "Akram Afif", "Mohammed Muntari", "Almoez Ali", "Hasan Al-Haydos", "Ismaeel Mohammad", "Abdulaziz Hatem", "Hamdi Harbaoui", "Ibrahim Alaaeldin")
),
"Switzerland": (
    pl("GK", "Yann Sommer", "Gregor Kobel", "Yvon Mvogo") +
    pl("DF", "Manuel Akanji", "Fabian Schär", "Ricardo Rodriguez", "Silvan Widmer", "Nico Elvedi", "Cedric Zesiger", "Leonidas Stergiou", "Edimilson Fernandes") +
    pl("MF", "Granit Xhaka", "Remo Freuler", "Denis Zakaria", "Michel Aebischer", "Vincent Sierro", "Ardon Jashari", "Christian Fassnacht") +
    pl("FW", "Breel Embolo", "Dan Ndoye", "Noah Okafor", "Zeki Amdouni", "Renato Steffen", "Andi Zeqiri", "Kwadwo Duah", "Ruben Vargas")
),
"Brazil": (
    pl("GK", "Alisson", "Ederson", "Bento") +
    pl("DF", "Marquinhos", "Éder Militão", "Gabriel Magalhães", "Danilo", "Wendell", "Bremer", "Yan Couto", "Guilherme Arana") +
    pl("MF", "Bruno Guimarães", "Lucas Paquetá", "João Gomes", "André", "Douglas Luiz", "Ederson Soares") +
    pl("FW", "Vinícius Júnior", "Rodrygo", "Endrick", "Raphinha", "Gabriel Jesus", "Gabriel Martinelli", "Savinho", "Estêvão", "Richarlison")
),
"Morocco": (
    pl("GK", "Yassine Bounou", "Munir Mohamedi", "Ahmed Reda Tagnaouti") +
    pl("DF", "Achraf Hakimi", "Nayef Aguerd", "Romain Saïss", "Noussair Mazraoui", "Jawad El Yamiq", "Badr Benoun", "Yahia Attiat-Allah", "Samy Mmaee") +
    pl("MF", "Sofyan Amrabat", "Azzedine Ounahi", "Brahim Díaz", "Selim Amallah", "Bilal El Khannouss", "Reda Tagnaouti", "Abdessamad Ezzalzouli") +
    pl("FW", "Hakim Ziyech", "Youssef En-Nesyri", "Youssef Adli", "Amir Richardson", "Abde Ezzalzouli", "Ilias Akhomach", "Amine Harit", "Soufiane Rahimi")
),
"Haiti": (
    pl("GK", "Romelys Noel", "Heritier Cazeau", "Johnny Placide") +
    pl("DF", "Derrick Etienne", "Frantzdy Pierrot", "Kevin Lafrance", "Mechack Jérôme", "Carlens Arcus", "Steeven Mukiele", "Bernadin Cidor", "Wilfried Gu") +
    pl("MF", "Thomas Gilles", "Duckens Nazon", "Jonel Predba", "Ronaldo Quiñones", "Kevin Lafrance", "Richardson Zephir", "Carlo Gelin") +
    pl("FW", "Duckens Nazon", "Frantzdy Pierrot", "Kervens Belfort", "Jonel Predba", "Romain Mundle", "Paul Geslin", "Christopher Pinol", "Maleik Fougère")
),
"Scotland": (
    pl("GK", "Angus Gunn", "Craig Gordon", "Liam Kelly") +
    pl("DF", "Andy Robertson", "Kieran Tierney", "John Souttar", "Scott McKenna", "Anthony Ralston", "Grant Hanley", "Jack Hendry", "Nathan Patterson") +
    pl("MF", "Scott McTominay", "John McGinn", "Billy Gilmour", "Callum McGregor", "Ryan Christie", "Kenny McLean", "Stuart Armstrong", "Ryan Jack") +
    pl("FW", "Lawrence Shankland", "Che Adams", "Lyndon Dykes", "James Forrest", "Ryan Fraser", "Ryan Jack", "Ben Doak", "Lee Johnson")
),
"USA": (
    pl("GK", "Matt Turner", "Ethan Horvath", "Patrick Schulte") +
    pl("DF", "Chris Richards", "Tim Ream", "Antonee Robinson", "Joe Scally", "Cameron Carter-Vickers", "Mark McKenzie", "Sam Vines", "Auston Trusty") +
    pl("MF", "Weston McKennie", "Tyler Adams", "Gio Reyna", "Yunus Musah", "Luca de la Torre", "Malik Tillman", "Johnny Cardoso", "Gianluca Busio") +
    pl("FW", "Christian Pulisic", "Folarin Balogun", "Tim Weah", "Caden Clark", "Josh Sargent", "Ricardo Pepi", "Jesús Ferreira", "Brandon Vazquez")
),
"Paraguay": (
    pl("GK", "Antony Silva", "Alfredo Aguilar", "Anthony Morinigo") +
    pl("DF", "Fabián Balbuena", "Gustavo Gómez", "Iván Piris", "Santiago Arzamendia", "Omar Alderete", "Robert Rojas", "Héctor Morales", "Ernesto Caballero") +
    pl("MF", "Miguel Almirón", "Mathías Villasanti", "Ángel Cardozo", "Gastón Giménez", "Hernán Pérez", "Édgar Benítez", "Richard Sánchez") +
    pl("FW", "Miguel Almirón", "Julio Enciso", "Antonio Sanabria", "Óscar Romero", "Gabriel Ávalos", "Roque Santa Cruz", "Rodrigo Rojas", "Federico Santander")
),
"Australia": (
    pl("GK", "Mat Ryan", "Danny Vukovic", "Joe Gauci") +
    pl("DF", "Harry Souttar", "Kye Rowles", "Nathaniel Atkinson", "Aziz Behich", "Joel King", "Miloš Degenek", "Thomas Deng", "Ryan Strain") +
    pl("MF", "Jackson Irvine", "Aaron Mooy", "Ajdin Hrustic", "Riley McGree", "Marco Tilio", "Lewis Miller", "Cameron Devlin") +
    pl("FW", "Socceroos Irankunda", "Martin Boyle", "Awer Mabil", "Adam Taggart", "Mitchell Duke", "Jason Cummings", "Nick Fitzgerald", "Kusini Yengi")
),
"Türkiye": (
    pl("GK", "Uğur Çakır", "Mert Günok", "Altay Bayındır") +
    pl("DF", "Ferdi Kadıoğlu", "Merih Demiral", "Samet Akaydın", "Zeki Çelik", "Mert Müldür", "Abdülkerim Bardakçı", "Ozan Kabak", "Çağlar Söyüncü") +
    pl("MF", "Hakan Çalhanoğlu", "Orkun Kökçü", "Salih Özcan", "Okay Yokuşlu", "Kaan Ayhan", "Kerem Aktürkoğlu", "Yunus Akgün") +
    pl("FW", "Arda Güler", "Serdar Dursun", "Kenan Yıldız", "Burak Yılmaz", "Baris Alper Yilmaz", "Yusuf Yazıcı", "Umut Meraş", "Emirhan Delibaş")
),
"Germany": (
    pl("GK", "Marc-André ter Stegen", "Oliver Baumann", "Alexander Nübel") +
    pl("DF", "Antonio Rüdiger", "Jonathan Tah", "Joshua Kimmich", "Maximilian Mittelstädt", "Nico Schlotterbeck", "Waldemar Anton", "Robin Koch", "Benjamin Henrichs") +
    pl("MF", "Toni Kroos", "İlkay Gündoğan", "Robert Andrich", "Pascal Groß", "Aleksandar Pavlović", "Leon Goretzka") +
    pl("FW", "Jamal Musiala", "Florian Wirtz", "Kai Havertz", "Leroy Sané", "Thomas Müller", "Niclas Füllkrug", "Deniz Undav", "Karim Adeyemi", "Tim Beier")
),
"Curaçao": (
    pl("GK", "Eloy Room", "Jairzinho Doornbusch", "Shiloh Bodak") +
    pl("DF", "Cuco Martina", "Dion Malone", "Genaro Snijders", "Gianni Zuiverloon", "Myron Boadu", "Tyronne Ebuehi", "Elvis Manu", "Rajiv van La Parra") +
    pl("MF", "Leandro Bacuna", "Vurnon Anita", "Carel Eiting", "Daishawn Redan", "Brandley Kuwas", "Rangelo Janga", "Geronimo Ruiz") +
    pl("FW", "Jafar Arias", "Juninho Bacuna", "Jamiro Monteiro", "Shane Peetersen", "Sheraldo Becker", "Ryan Koolwijk", "Giliano Wijnaldum", "Gio Michels")
),
"Ivory Coast": (
    pl("GK", "Yahia Fofana", "Ali Badra Sangaré", "Badra Ali Sangaré") +
    pl("DF", "Jonathan Kodjia", "Willy Boly", "Odilon Kossounou", "Eric Bailly", "Serge Aurier", "Jean-Michaël Seri", "Emmanuel Agbadou", "Simon Deli") +
    pl("MF", "Franck Kessié", "Seko Fofana", "Ibrahim Sangaré", "Cheick Doucouré", "Adama Traoré", "Gradel Max-Alain", "Yves Bissouma") +
    pl("FW", "Sébastien Haller", "Simon Adingra", "Nicolas Pépé", "Wilfried Bony", "Max-Alain Gradel", "Jeremie Boga", "Lassine Sinayoko", "Jonathan Kodjia")
),
"Ecuador": (
    pl("GK", "Hernán Galíndez", "Alexander Domínguez", "Renato Ibarra") +
    pl("DF", "Piero Hincapié", "Félix Torres", "Pervis Estupiñán", "Byron Castillo", "Ángelo Preciado", "Diego Palacios", "José Hurtado", "Jhegson Méndez") +
    pl("MF", "Moisés Caicedo", "Carlos Gruezo", "Ángel Mena", "Jhegson Méndez", "Alan Franco", "Gledy Simons", "Anthony Valencia") +
    pl("FW", "Kendry Páez", "Enner Valencia", "Michael Estrada", "Kevin Rodríguez", "Leonardo Campana", "Jordy Caicedo", "Jeremy Sarmiento", "Djorkaeff Reasco")
),
"Netherlands": (
    pl("GK", "Bart Verbruggen", "Justin Bijlow", "Mark Flekken") +
    pl("DF", "Virgil van Dijk", "Nathan Aké", "Denzel Dumfries", "Matthijs de Ligt", "Jeremie Frimpong", "Ian Maatsen", "Micky van de Ven", "Jurrien Timber") +
    pl("MF", "Frenkie de Jong", "Tijjani Reijnders", "Xavi Simons", "Ryan Gravenberch", "Teun Koopmeiners", "Davy Klaassen", "Joey Veerman") +
    pl("FW", "Cody Gakpo", "Donyell Malen", "Wout Weghorst", "Brian Brobbey", "Steven Bergwijn", "Joshua Zirkzee", "Memphis Depay")
),
"Japan": (
    pl("GK", "Zion Suzuki", "Mitch Langerak", "Daniel Schmidt") +
    pl("DF", "Ko Itakura", "Maya Yoshida", "Shogo Taniguchi", "Miki Yamane", "Hiroki Ito", "Takehiro Tomiyasu", "Yukinari Sugawara", "Yuto Nagatomo") +
    pl("MF", "Wataru Endo", "Hidemasa Morita", "Takefusa Kubo", "Ritsu Doan", "Daichi Kamada", "Ao Tanaka", "Reo Hatate") +
    pl("FW", "Kaoru Mitoma", "Ayase Ueda", "Daizen Maeda", "Takumi Minamino", "Junya Ito", "Keito Nakamura", "Kou Itakura", "Kyogo Furuhashi")
),
"Sweden": (
    pl("GK", "Robin Olsen", "Karl-Johan Johnsson", "Andreas Linde") +
    pl("DF", "Victor Lindelöf", "Isak Hien", "Carl Starfelt", "Ludwig Augustinsson", "Niclas Eliasson", "Emil Holm", "Jesper Karlsson", "Marcus Danielson") +
    pl("MF", "Dejan Kulusevski", "Jens Cajuste", "Emil Forsberg", "Mattias Svanberg", "Viktor Gyökeres", "Jesper Löfgren", "Lucas Bergvall") +
    pl("FW", "Alexander Isak", "Viktor Gyökeres", "Anthony Elanga", "Robin Quaison", "Svante Ingelsson", "John Guidetti", "Pontus Almqvist", "Samuel Addo Appiah")
),
"Tunisia": (
    pl("GK", "Aymen Dahmen", "Mouez Hassen", "Farouk Ben Mustapha") +
    pl("DF", "Dylan Bronn", "Montassar Talbi", "Bilel Ifa", "Ali Abdi", "Hamza Rafia", "Wajdi Kechrida", "Nader Ghandri", "Mohamed Ben Hamida") +
    pl("MF", "Ellyes Skhiri", "Aïssa Laïdouni", "Hannibal Mejbri", "Youssef Msakni", "Naim Sliti", "Ben Romdhane", "Mohamed Ali Ben Romdhane") +
    pl("FW", "Wahbi Khazri", "Taha Yassine Khenissi", "Seifeddine Jaziri", "Saif-Eddine Khaoui", "Ammar Hatem", "Elias Achouri", "Issam Jebali", "Hamdi Harbaoui")
),
"Belgium": (
    pl("GK", "Koen Casteels", "Matz Sels", "Thomas Kaminski") +
    pl("DF", "Wout Faes", "Jan Vertonghen", "Thomas Meunier", "Timothy Castagne", "Zeno Debast", "Arthur Theate", "Maxim De Cuyper", "Killian Sardella") +
    pl("MF", "Kevin De Bruyne", "Amadou Onana", "Youri Tielemans", "Arthur Vermeeren", "Orel Mangala", "Charles De Ketelaere", "Arne Engels") +
    pl("FW", "Romelu Lukaku", "Leandro Trossard", "Jérémy Doku", "Lois Openda", "Johan Bakayoko", "Dodi Lukebakio", "Alexis Saelemaekers", "Loïs Openda")
),
"Egypt": (
    pl("GK", "Mohamed El-Shenawy", "Ahmed El-Shenawy", "Mohamed Shobeir") +
    pl("DF", "Ahmed Hegazi", "Ahmed Abdelmonem", "Mohamed Hany", "Omar Kamal", "Mahmoud Hamdi", "Akram Tawfik", "Zizo", "Ibrahim Adel") +
    pl("MF", "Mohamed Elneny", "Tarek Hamed", "Amr El-Sulaya", "Omar Marmoush", "Emam Ashour", "Ibrahim Adel", "Mohamed Abdel Moneim") +
    pl("FW", "Mohamed Salah", "Mostafa Mohamed", "Mahmoud Trezeguet", "Omar Marmoush", "Zizo", "Ahmed Rayan", "Amr El-Sulaya", "Karim Hamdallah")
),
"Cape Verde": (
    pl("GK", "Vozinha", "Marcelo Boavida", "José Carlos Silva") +
    pl("DF", "Zé Luís", "Kenny Rocha Santos", "Stopira", "Fortes", "Marco Soares", "Lisandro", "Brendan Ferreira", "Diney") +
    pl("MF", "Garry Rodrigues", "Ryan Mendes", "Kiki Rocha", "Pina", "Júlio Tavares", "Andrezinho", "Augusto Meideros") +
    pl("FW", "Bebé", "Gilson Benchimol", "Mickaël Pote", "Vagner", "Deroy Duarte", "Carlos Vaz Tê", "Jovane Cabral", "Ryan Mendes")
),
"Saudi Arabia": (
    pl("GK", "Mohammed Al-Owais", "Sultan Al-Ghannam", "Nawaf Al-Aqidi") +
    pl("DF", "Saud Abdulhamid", "Ali Al-Bulaihi", "Abdulelah Al-Amri", "Hassan Tambakti", "Yasser Al-Shahrani", "Abdullah Madu", "Salman Al-Faraj", "Sultan Ghannam") +
    pl("MF", "Sami Al-Najei", "Nasser Al-Dawsari", "Riyadh Mahrez", "Mohamed Kanno", "Ali Al-Hassan", "Abdulrahman Al-Aboud", "Mishal Al-Harbi") +
    pl("FW", "Saleh Al-Shehri", "Firas Al-Buraikan", "Abdullah Al-Hamdan", "Hattan Bahebri", "Mohammed Maran", "Muhannad Al-Shanqeeti", "Abdullah Radif", "Abdulrahman Al-Khateeb")
),
"New Zealand": (
    pl("GK", "Oliver Sail", "Stefan Marinovic", "Rhys Bartlett") +
    pl("DF", "Winston Reid", "Michael Boxall", "Liberato Cacace", "Matthew Garbett", "Tim Payne", "Joe Bell", "Marko Grgić", "Callan Elliot") +
    pl("MF", "Clayton Lewis", "Elijah Just", "Alex Rufer", "Callum McCowatt", "Sarpreet Singh", "Louis Fenton", "Ryan Thomas") +
    pl("FW", "Chris Wood", "Liberato Cacace", "Kosta Barbarouses", "Sarpreet Singh", "Ben Old", "Callan Elliot", "Matthew Garbett", "Hamish Watson")
),
"Spain": (
    pl("GK", "Unai Simón", "David Raya", "Álex Remiro") +
    pl("DF", "Dani Carvajal", "Robin Le Normand", "Aymeric Laporte", "Marc Cucurella", "Alejandro Grimaldo", "Pau Cubarsí", "Jordi Alba", "Nacho Fernández") +
    pl("MF", "Rodri", "Fabián Ruiz", "Pedri", "Dani Olmo", "Mikel Merino", "Martín Zubimendi", "Gavi") +
    pl("FW", "Lamine Yamal", "Álvaro Morata", "Nico Williams", "Mikel Oyarzabal", "Ferran Torres", "Joselu", "Ayoze Pérez", "Gerard Moreno")
),
"Senegal": (
    pl("GK", "Édouard Mendy", "Alfred Gomis", "Seny Dieng") +
    pl("DF", "Kalidou Koulibaly", "Abdou Diallo", "Formose Mendy", "Pape Abou Cissé", "Moussa Niakhaté", "Ismail Jakobs", "Youssouf Sabaly", "Fodé Ballo-Touré") +
    pl("MF", "Idrissa Gueye", "Nampalys Mendy", "Cheikhou Kouyaté", "Pape Matar Sarr", "Lamine Camara", "Pathé Ciss", "Dion Lopy") +
    pl("FW", "Sadio Mané", "Ismaïla Sarr", "Nicolas Jackson", "Boulaye Dia", "Habib Diallo", "Iliman Ndiaye", "Abdallah Sima", "Bamba Dieng")
),
"Jordan": (
    pl("GK", "Yazeed Abulaila", "Mohammad Riyad", "Elias Al-Zoubi") +
    pl("DF", "Bashar Bani Yaseen", "Ahmad Saleh", "Khaled Al-Ajalin", "Ali Olwan", "Mohammad Hemd", "Baha'a Abdelrahman", "Ahmad Al-Naimat", "Saleh Rateb") +
    pl("MF", "Yazan Al-Naimat", "Ahmad Habawneh", "Mohammad Bani Yaseen", "Musa Al-Taamari", "Ahmad Rawabdeh", "Mohammad Abu Almajd", "Ismail Azzam") +
    pl("FW", "Musa Al-Taamari", "Baha' Faisal", "Hamza Al-Dardour", "Ali Olwan", "Salam Ananzeh", "Qusai Abu Alia", "Ibrahim Abu Hamdan", "Muath Kasasbeh")
),
"France": (
    pl("GK", "Mike Maignan", "Brice Samba", "Alphonse Areola") +
    pl("DF", "William Saliba", "Dayot Upamecano", "Ibrahima Konaté", "Jules Koundé", "Theo Hernández", "Benjamin Pavard", "Jonathan Clauss", "Ferland Mendy") +
    pl("MF", "Eduardo Camavinga", "Aurélien Tchouaméni", "Adrien Rabiot", "Antoine Griezmann", "Warren Zaïre-Emery", "Matteo Guendouzi") +
    pl("FW", "Kylian Mbappé", "Ousmane Dembélé", "Bradley Barcola", "Marcus Thuram", "Olivier Giroud", "Randal Kolo Muani", "Kingsley Coman", "Michael Olise", "Christopher Nkunku")
),
"Norway": (
    pl("GK", "Ørjan Nyland", "Jørgen Strand Larsen", "Matz Sels") +
    pl("DF", "Stefan Strandberg", "Leo Østigård", "Julian Ryerson", "Birger Meling", "Kristoffer Ajer", "Marcus Holmgren Pedersen", "Morten Thorsby", "Stian Gregersen") +
    pl("MF", "Martin Ødegaard", "Sander Berge", "Mats Møller Dæhli", "Fredrik Aursnes", "Patrick Berg", "Kristian Thorstvedt", "Ola Solbakken") +
    pl("FW", "Erling Haaland", "Alexander Sørloth", "Mohamed Elyounoussi", "Jørgen Strand Larsen", "Antonio Nusa", "Ole Sæter", "Viktor Boniface", "Oscar Bobb")
),
"Algeria": (
    pl("GK", "Raïs M'Bolhi", "Alexandre Oukidja", "Mathieu Gorgelin") +
    pl("DF", "Youcef Atal", "Aissa Mandi", "Ramy Bensebaini", "Zinedine Ferhat", "Andy Delort", "Hicham Boudaoui", "Djamel Benlamri", "Rayan Aït-Nouri") +
    pl("MF", "Nabil Bentaleb", "Ismael Bennacer", "Houssem Aouar", "Ramiz Zerrouki", "Mohamed Amine Tougai", "Zinedine Ferhat", "Yacine Brahimi") +
    pl("FW", "Riyad Mahrez", "Islam Slimani", "Baghdad Bounedjah", "Sofiane Bendebka", "Amir Sayoud", "Said Benrahma", "Adam Ounas", "Nassim Bouchareb")
),
"Austria": (
    pl("GK", "Patrick Pentz", "Daniel Bachmann", "Alexander Schlager") +
    pl("DF", "Stefan Posch", "Kevin Danso", "Philipp Lienhart", "Phillipp Mwene", "David Alaba", "Maximilian Wöber", "Leopold Querfeld", "Phillipp Sturm") +
    pl("MF", "Nicolas Seiwald", "Konrad Laimer", "Marcel Sabitzer", "Christoph Baumgartner", "Florian Grillitsch", "Florian Kainz", "Patrick Wimmer") +
    pl("FW", "Michael Gregoritsch", "Marko Arnautović", "Christoph Monschein", "Sasa Kalajdzic", "Andreas Weimann", "Patrick Griesser", "Karim Adeyemi", "Romano Schmid")
),
"Portugal": (
    pl("GK", "Diogo Costa", "Rui Patrício", "José Sá") +
    pl("DF", "Rúben Dias", "Nuno Mendes", "João Cancelo", "Gonçalo Inácio", "Diogo Dalot", "Pepe", "António Silva", "Semedo") +
    pl("MF", "João Palhinha", "Vitinha", "Bruno Fernandes", "Otávio", "Rúben Neves", "João Neves", "Bernardo Silva") +
    pl("FW", "Cristiano Ronaldo", "Rafael Leão", "Diogo Jota", "Gonçalo Ramos", "João Félix", "Pedro Neto", "Francisco Conceição")
),
"DR Congo": (
    pl("GK", "Joël Kiassumbua", "Vito Mannone", "Ibrahim Mounkoro") +
    pl("DF", "Chancel Mbemba", "Arthur Masuaku", "Pierre Kalulu", "Dylan Batubinsika", "Yannick Bolasie", "Mulamba Diakité", "Marcel Tisserand", "Denis Bouanga") +
    pl("MF", "Yannick Ferreira-Carrasco", "Théo Bongonda", "Youssouf Mulumbu", "Gael Kakuta", "Cédric Bakambu", "Silas Wissa", "Jean-Marc Makusu Mundele") +
    pl("FW", "Silas Wissa", "Cédric Bakambu", "Samuel Bastien", "Benjamin Tetteh", "Georginio Rutter", "Ângel Gomes", "Emmanuel Mbangula", "Dodi Lukebakio")
),
"Uzbekistan": (
    pl("GK", "Eldor Shomurodov", "Otabek Shukurov", "Bunyod Yusupov") +
    pl("DF", "Dostonbek Khamdamov", "Abbosbek Fayzullaev", "Jasurbek Yakhshiboev", "Sanjar Tursunov", "Khojiakbar Alijonov", "Oybek Bozorov", "Temur Jumayev", "Sherzod Qodirov") +
    pl("MF", "Otabek Shukurov", "Jaloliddin Masharipov", "Islombek Kobilov", "Ilkhomjon Shamsiev", "Abdukodir Khusanov", "Dostonbek Khamdamov", "Doniyor Ergashev") +
    pl("FW", "Eldor Shomurodov", "Abbosbek Fayzullaev", "Bobur Abdikholikov", "Sherzod Nasrullayev", "Laziz Azimov", "Jamshid Iskanderov", "Marat Bikmaev", "Dostonbek Khamdamov")
),
"Colombia": (
    pl("GK", "David Ospina", "Camilo Vargas", "Kevin Mier") +
    pl("DF", "Dávinson Sánchez", "William Tesillo", "Stefan Medina", "Johan Mojica", "Yerry Mina", "Jhon Lucumí", "Daniel Muñoz", "Carlos Cuesta") +
    pl("MF", "Juan Cuadrado", "Wilmar Barrios", "James Rodríguez", "Lerma", "Nicolás Gómez", "Richard Ríos", "Jhon Arias") +
    pl("FW", "Luis Díaz", "Falcao", "Rafael Santos Borré", "Jhon Córdoba", "Radamel Falcao", "Cucho Hernández", "Sinisterra", "Jhon Durán")
),
"England": (
    pl("GK", "Jordan Pickford", "Dean Henderson", "James Trafford") +
    pl("DF", "Reece James", "John Stones", "Marc Guéhi", "Ben Chilwell", "Kyle Walker", "Trent Alexander-Arnold", "Harry Maguire", "Lewis Dunk") +
    pl("MF", "Declan Rice", "Jude Bellingham", "Kobbie Mainoo", "Curtis Jones", "Jarrod Bowen", "Cole Palmer", "Phil Foden") +
    pl("FW", "Harry Kane", "Bukayo Saka", "Anthony Gordon", "Ollie Watkins", "Ivan Toney", "Marcus Rashford", "Demarai Gray", "Raheem Sterling")
),
"Croatia": (
    pl("GK", "Dominik Livaković", "Ivica Ivušić", "Nediljko Labrović") +
    pl("DF", "Joško Gvardiol", "Josip Šutalo", "Josip Stanisić", "Martin Erlić", "Borna Sosa", "Marin Pongračić", "Domagoj Vida", "Josip Juranović") +
    pl("MF", "Luka Modrić", "Mateo Kovačić", "Marcelo Brozović", "Mario Pašalić", "Luka Ivanušec", "Lovro Majer", "Nikola Vlašić") +
    pl("FW", "Andrej Kramarić", "Ante Budimir", "Bruno Petković", "Ivan Perišić", "Marko Pjaca", "Kristijan Jakić", "Toni Šunjić", "Josip Brekalo")
),
"Iran": (
    pl("GK", "Alireza Beiranvand", "Hossein Hosseini", "Payam Niazmand") +
    pl("DF", "Milad Mohammadi", "Shojae Khalilzadeh", "Sadegh Moharrami", "Mohammad Karimi", "Ehsan Hajsafi", "Hossein Kanaanizadegan", "Abolfazl Jalali", "Farshid Esmaeili") +
    pl("MF", "Saeid Ezatolahi", "Saman Ghoddos", "Omid Noorafkan", "Mehdi Torabi", "Ahmad Nourollahi", "Alireza Jahanbakhsh", "Karim Ansarifard") +
    pl("FW", "Mehdi Taremi", "Sardar Azmoun", "Alireza Jahanbakhsh", "Morteza Pouraliganji", "Vahid Amiri", "Ali Gholizadeh", "Majid Hosseini", "Karim Ansarifard")
),
"Ghana": (
    pl("GK", "Lawrence Ati-Zigi", "Jojo Wollacott", "Ibrahim Danlad") +
    pl("DF", "Alexander Djiku", "Tariq Lamptey", "Abdul Rahman Baba", "Daniel Amartey", "Mohammed Salisu", "Gideon Mensah", "Alidu Seidu", "Andy Yiadom") +
    pl("MF", "Thomas Partey", "Mohammed Kudus", "Daniel-Kofi Kyereh", "Iddrisu Baba", "Andre Ayew", "Abdul Samed Salis", "Ernest Nuamah") +
    pl("FW", "Inaki Williams", "Antoine Semenyo", "Jordan Ayew", "Osman Bukari", "Emmanuel Gyasi", "Ernest Nuamah", "Fatawu Issahaku", "Felix Afena-Gyan")
),
"Argentina": (
    pl("GK", "Emiliano Martínez", "Gerónimo Rulli", "Franco Armani") +
    pl("DF", "Cristian Romero", "Lisandro Martínez", "Nicolás Otamendi", "Nahuel Molina", "Nicolás Tagliafico", "Germán Pezzella", "Gonzalo Montiel", "Marcos Acuña") +
    pl("MF", "Rodrigo De Paul", "Alexis Mac Allister", "Enzo Fernández", "Leandro Paredes", "Giovanni Lo Celso", "Exequiel Palacios", "Guido Rodríguez") +
    pl("FW", "Lionel Messi", "Julián Álvarez", "Ángel Di María", "Lautaro Martínez", "Nicolás González", "Alejandro Garnacho", "Paulo Dybala", "Joaquín Correa")
),
"Uruguay": (
    pl("GK", "Fernando Muslera", "Sergio Rochet", "Sebastián Sosa") +
    pl("DF", "José María Giménez", "Ronald Araújo", "Nahitan Nández", "Martín Cáceres", "Mathías Olivera", "Sebastián Coates", "Santiago Bueno", "Giovanni González") +
    pl("MF", "Federico Valverde", "Manuel Ugarte", "Nicolás De la Cruz", "Rodrigo Bentancur", "Giorgian de Arrascaeta", "Facundo Pellistri", "Mathías Olivera") +
    pl("FW", "Darwin Núñez", "Luis Suárez", "Edinson Cavani", "Luciano Rodríguez", "Facundo Torres", "Maxi Gómez", "Brian Ocampo", "Agustín Canobbio")
),
"Panama": (
    pl("GK", "Luis Mejía", "José Calderón", "Orlando Mosquera") +
    pl("DF", "Fidel Escobar", "Harold Cummings", "Anibal Godoy", "Adolfo Machado", "Michael Murillo", "Armando Cooper", "Andrés Andrade", "Eric Davis") +
    pl("MF", "Adalberto Carrasquilla", "Édgar Bárcenas", "Aníbal Godoy", "Rolando Blackman", "Jorman Aguilar", "Alberto Quintero", "José Fajardo") +
    pl("FW", "Roberto Nurse", "Naldo Moreno", "Éric Davis", "Cesar Blackman", "Gabriel Torres", "Ismael Díaz", "Blas Pérez", "Alberto Quintero")
),
"Iraq": (
    pl("GK", "Jalal Hassan", "Dhurgham Ismail", "Mohammed Hameed") +
    pl("DF", "Rebin Sulaka", "Ali Adnan", "Murtadha Mahdi", "Saad Natiq", "Mustafa Nadhim", "Ahmed Ibrahim", "Bassam Riad", "Hussein Ali") +
    pl("MF", "Safaa Hadi", "Ahmed Yasin", "Amjad Attwan", "Karrar Hamad", "Mohanad Jerew", "Ali Husain", "Alaa Abdulmajeed") +
    pl("FW", "Aymen Hussein", "Mohanad Abdulraheem", "Humam Tariq", "Ali Jasim", "Omar Sabah", "Thaer Krouma", "Wael Radhi", "Alaa Mhawi")
),
}

# Build group lookup: team → group letter
TEAM_TO_GROUP = {}
for grp, teams in GROUPS.items():
    for t in teams:
        TEAM_TO_GROUP[t] = grp

data = {
    "groups": GROUPS,
    "confederations": CONFEDERATIONS,
    "team_to_group": TEAM_TO_GROUP,
    "squads": RAW_SQUADS,
}

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False))
players_total = sum(len(v) for v in RAW_SQUADS.values())
print(f"Written {OUT}  ({len(RAW_SQUADS)} teams, {players_total} players)")
