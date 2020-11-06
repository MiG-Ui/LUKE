﻿import sys
from queue import Queue
from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.QtWidgets import * 

# The new Stream Object which replaces the default stream associated with sys.stdout
# This object just puts data in a queue!
class WriteStream(object):
    def __init__(self,queue):
        self.queue = queue

    def write(self, text):
        self.queue.put(text)

    def flush(self):
        pass

# A QObject (to be run in a QThread) which sits waiting for data to come through a Queue.Queue().
# It blocks until data is available, and one it has got something from the queue, it sends
# it to the "MainThread" by emitting a Qt Signal 
class MyReceiver(QObject):
    signal = pyqtSignal(str)

    def __init__(self,queue,*args,**kwargs):
        QObject.__init__(self,*args,**kwargs)
        self.queue = queue

    @pyqtSlot()
    def run(self):
        while True:
            text = self.queue.get()
            self.signal.emit(text)

class OutputViewer(QWidget):

    updateLogSignal = pyqtSignal(str)

    def __init__(self,*args,**kwargs):
        QWidget.__init__(self,*args,**kwargs)
        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.clear)
        self.layout = QVBoxLayout(self)
        self.layout.addWidget(self.text_edit)
        self.layout.addWidget(self.clear_button)
        self.queue = None

        self.latestLine = ""

    def redirectStdOut(self):
        # Create Queue and redirect sys.stdout to this queue
        self.queue = Queue()
        sys.stdout = WriteStream(self.queue)

        # Create thread that will listen on the other end of the queue, and send the text to the textedit in our application
        self.thread = QThread()
        self.receiver = MyReceiver(self.queue)
        self.receiver.signal.connect(self.appendText)
        self.receiver.moveToThread(self.thread)
        self.thread.started.connect(self.receiver.run)
        self.thread.start()

    @pyqtSlot(str)
    def appendText(self,text):
        self.text_edit.moveCursor(QTextCursor.End)
        self.text_edit.insertPlainText( text )

        lines = self.text_edit.toPlainText().splitlines(False)
        if len(lines) > 0:
            self.updateLogSignal.emit(lines[-1])

    def clear(self):
        self.text_edit.clear()

if __name__ == "__main__":
    # An example QObject (to be run in a QThread) which outputs information with print
    class LongRunningThing(QObject):
        @pyqtSlot()
        def run(self):
            for i in range(1000):
                print(i)

    # An Example application QWidget containing the textedit to redirect stdout to
    class MyApp(QWidget):
        def __init__(self,*args,**kwargs):
            QWidget.__init__(self,*args,**kwargs)

            self.thread = None
            self.layout = QVBoxLayout(self)
            #self.textedit = QTextEdit()
            self.output_viewer = OutputViewer()
            self.output_viewer.redirectStdOut()
            self.button = QPushButton('start long running thread')
            self.button.clicked.connect(self.start_thread)
            #self.layout.addWidget(self.textedit)
            self.layout.addWidget(self.output_viewer)
            self.layout.addWidget(self.button)

        #@pyqtSlot(str)
        #def appendText(self,text):
        #    self.textedit.moveCursor(QTextCursor.End)
        #    self.textedit.insertPlainText( text )

        @pyqtSlot()
        def start_thread(self):
            if self.thread:
                self.thread.quit()
                self.thread.wait()

            self.thread = QThread()
            self.long_running_thing = LongRunningThing()
            self.long_running_thing.moveToThread(self.thread)
            self.thread.started.connect(self.long_running_thing.run)
            self.thread.start()

    # Create QApplication and QWidget
    qapp = QApplication(sys.argv)  
    app = MyApp()
    app.show()

    qapp.exec_()