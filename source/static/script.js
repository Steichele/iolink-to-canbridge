
$(document).ready(function() {
    
        
    setInterval(function () {
        fetch(window.location.href)
        .then(function (response) {
            return response.text();
            })
            .then(function (html) {
                const tempDiv = document.createElement("div");
                tempDiv.innerHTML = html;
                
                const newDecoded = tempDiv.querySelector("#decoded");
                const currentDecoded = document.querySelector("#decoded");
                
                if (newDecoded && currentDecoded) {
                    currentDecoded.innerHTML = newDecoded.innerHTML;
                }
            })
            .catch(function (error) {
                console.error("Fehler beim Aktualisieren von #decoded:", error);
            });
    }, 500);

    setInterval(function () {
        fetch(window.location.href)
        .then(function (response) {
            return response.text();
            })
            .then(function (html) {
                const tempDiv = document.createElement("div");
                tempDiv.innerHTML = html;
                
                const newDecoded = tempDiv.querySelector("#files");
                const currentDecoded = document.querySelector("#files");
                
                if (newDecoded && currentDecoded) {
                    currentDecoded.innerHTML = newDecoded.innerHTML;
                }
            })
            .catch(function (error) {
                console.error("Fehler beim Aktualisieren von #files:", error);
            });
    }, 3000);
        
    setInterval(function(){
        $("#status").load(window.location.href + " #status" );
    }, 500);
    setInterval(function(){
        $("#sysinfo").load(window.location.href + " #sysinfo" );
    }, 2000);
    setInterval(function(){
            $("#cancel").load(window.location.href + " #cancel" );
    }, 2000);

    // Toggle-Funktion f�r #canData
    $("#canData").hide(); // initial verstecken
    $("#toggleCanData").click(function() {
        $("#canData").toggle(); // zeigt oder versteckt den Div
    });

        
    setInterval(function () {
            $('#canData').load(`http://${myip}:8081/trace`);
    }, 200)


});

function toggleMenu() {
    document.getElementById("actionMenu").classList.toggle("show");
}

window.onclick = function(event) {
    if (!event.target.closest('.action-container')) {
    document.getElementById("actionMenu").classList.remove("show");
    }
}


